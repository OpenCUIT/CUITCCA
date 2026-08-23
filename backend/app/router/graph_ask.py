"""自动路由端点：把"标准问答 vs Agent 模式"这个架构决策从用户界面上收回来，
由 handlers/auto_router.py 按检索置信度自动判定。学生问一句话，不需要先
弄懂"我这个问题算不算复杂"才能选对按钮。

不改动既有端点——/chat_stream、/agent_chat_stream 等继续保留原样（有测试
覆盖，也是分别演示两条链路能力的入口），这里只是统一入口，内部按需
分发到 QAWorkflow 或 FunctionAgent。

表单参数（query 之外全部可选）：

- ``conversation_id``：多会话隔离键，见 graph_session.effective_client_id；
- ``skip_cache``：跳过语义缓存查询（前端"重新生成"用——同一问题逐字重发
  必然命中 auto 缓存拿到一模一样的旧答案，重新生成就没有意义了）；
- ``pop_last``：处理前先撤回会话里最后一轮问答（同样是"重新生成"用，
  见 graph_session.pop_last_exchange）。

每个请求结束（无论成功/失败/中断）都会写一条 AI 请求日志
（utils/ai_metrics.py：model/mode/latency/tool_calls/检索条数/token 估算），
供 /manage/ai-metrics 聚合查看——AI 应用的成本与性能监控素材。
"""
import json
import time

from fastapi import APIRouter, Form, Request
from llama_index.core.base.llms.types import ChatMessage, MessageRole
from llama_index.core.schema import NodeWithScore, TextNode
from router.graph_qa import _PrefetchedNodesRetriever
from router.graph_session import (
    _chat_histories,
    _last_query_response,
    effective_client_id,
    pop_last_exchange,
)
from starlette.responses import StreamingResponse
from utils import ai_metrics
from utils.logger import error_logger, query_logger

ask_app = APIRouter()


@ask_app.post("/ask_stream")
async def ask_stream(
    request: Request,
    query: str = Form(max_length=5000),
    conversation_id: str = Form(None, max_length=64),
    skip_cache: bool = Form(False),
    pop_last: bool = Form(False),
):
    """自动路由的统一流式问答入口。

    NDJSON（跟 ``/agent_chat_stream`` 同一套协议），事件类型：

    - ``route``：路由判定一算完立刻发出的第一个事件，``{"mode": "standard"
      |"agent"|"cache", "reason": "..."}``——前端用它展示"这次走了哪条路"。
      ``mode`` 多一个 ``cache``：语义缓存（``handlers/qa_cache.py``）命中，
      直接复用历史答案，连路由判定都没跑。
    - ``token``：答案增量文本，``{"content": "..."}``；standard/agent 两个
      分支统一用这个字段名（agent 分支本身就是这么发的，standard 分支这里
      特意把 ``QAWorkflow.TokenEvent.token`` 也包成同名字段，前端只需要
      一个解析器）。缓存命中分支同样发一个完整的 token 事件。
    - ``tool_call``/``tool_result``：只有 agent 分支会有（standard 分支走的
      ``QAWorkflow`` 没有工具调用），跟 ``/agent_chat_stream`` 语义一致，
      前端复用同一套"工具调用轨迹"展示逻辑。
    - ``done``：本轮回答结束，带最终 ``response``（agent 分支还带
      ``truncated``）。
    - ``suggestions``：``{"suggestions": [...]}``，在 ``done`` 之后单独发出。
      追问建议是答案讲完之后才生成的（见
      ``handlers.auto_router.generate_followup_suggestions`` docstring），
      失败/超时会拿到空数组，不代表这轮回答本身失败了。缓存命中分支不发
      追问建议（命中就是冲着"快"去的，不再搭一次最多 8 秒的 LLM 调用），
      直接发空数组。
    - ``error``：出错兜底，跟其余端点用同一套兜底文案。

    会话历史/来源记录跟 ``/agent_chat_stream`` 保持同样的处理方式：出错时
    不写空的 assistant 消息进历史（避免下一轮 condense/agent 请求带着一条
    空消息，见 ``/agent_chat_stream`` 里那段注释）。
    """
    from agents.agent_workflow import ToolCallTrace, extract_source_nodes, stream_agent_events
    from handlers.auto_router import MODE_AGENT, generate_followup_suggestions, route_query
    from handlers.qa_cache import CachedEntry
    from handlers.qa_workflow import QAWorkflow, TokenEvent

    client_id = effective_client_id(request, conversation_id)
    if pop_last:
        pop_last_exchange(client_id)
    history: list[ChatMessage] = list(_chat_histories.get(client_id) or [])
    query = query.strip()
    query_logger.info(f"ask_stream: {query}")

    # AI 请求日志（utils/ai_metrics.py）：请求开始计时，事件流里逐步补充
    # mode/first_token/tool_calls/retrieval_count，结束时 finally 统一落盘。
    started = time.monotonic()
    metrics: dict = {
        "endpoint": "/graph/ask_stream",
        "mode": "error",
        "first_token_ms": None,
        "tool_calls": 0,
        "retrieval_count": 0,
        "cache_hit": False,
        "truncated": False,
        "query_chars": len(query),
        "answer_chars": 0,
        "est_completion_tokens": 0,
    }
    first_token_at: float | None = None

    async def _event_gen():
        from handlers import qa_cache

        nonlocal first_token_at
        try:
            # 语义缓存优先：命中直接回答案，跳过路由判定/检索/LLM 生成（本地嵌入
            # 毫秒级成本，miss 也不亏）。lookup 自己保证不抛异常，这里不再包 try。
            # skip_cache（重新生成）时跳过。
            cached: CachedEntry | None = None
            if not skip_cache:
                cached = await qa_cache.lookup(query)
            if cached is not None:
                kind_label = "人工沉淀" if cached.kind == "curated" else "历史问答"
                metrics.update(
                    mode="cache", cache_hit=True,
                    answer_chars=len(cached.answer),
                    est_completion_tokens=ai_metrics.estimate_tokens(cached.answer),
                    retrieval_count=1,
                )
                yield json.dumps(
                    {
                        "type": "route",
                        "mode": "cache",
                        "reason": f"命中语义缓存（{kind_label}），已跳过检索与生成",
                    },
                    ensure_ascii=False,
                ) + "\n"
                yield json.dumps(
                    {"type": "token", "content": cached.answer}, ensure_ascii=False
                ) + "\n"
                yield json.dumps(
                    {
                        "type": "done",
                        "response": cached.answer,
                        "tool_call_count": 0,
                        "truncated": False,
                    },
                    ensure_ascii=False,
                ) + "\n"
                yield json.dumps({"type": "suggestions", "suggestions": []}, ensure_ascii=False) + "\n"

                # 缓存条目里存的来源片段重建一个占位来源节点：命中时 /query_sources
                # 仍然有东西可显示，前端引用列表不空。
                if cached.source_text:
                    source_node = NodeWithScore(
                        node=TextNode(
                            text=cached.source_text,
                            metadata={"file_name": cached.source_file or None},
                        ),
                        score=1.0,
                    )
                    _last_query_response.set(client_id, [source_node])
                history.append(ChatMessage(role=MessageRole.USER, content=query))
                history.append(ChatMessage(role=MessageRole.ASSISTANT, content=cached.answer))
                _chat_histories.set(client_id, history)
                return

            try:
                decision = await route_query(query, chat_history=history)
            except Exception as e:
                error_logger.error(f"ask_stream route_query error: {e}")
                yield json.dumps({"type": "error", "message": "出错了，请稍后再试一下吧"}, ensure_ascii=False) + "\n"
                return

            metrics["mode"] = decision.mode
            yield json.dumps(
                {"type": "route", "mode": decision.mode, "reason": decision.reason}, ensure_ascii=False
            ) + "\n"

            final_response = ""
            errored = False
            source_nodes: list[NodeWithScore] = []

            if decision.mode == MODE_AGENT:
                token_parts: list[str] = []
                tool_calls: list[ToolCallTrace] = []
                try:
                    # agent 分支用原始 query（不是路由判定阶段压缩好的
                    # decision.query_str）——理由见 handlers/auto_router.py 模块
                    # docstring"agent 分支为什么不传压缩后的问题"一节。
                    async for event in stream_agent_events(query, chat_history=history):
                        if event["type"] == "token":
                            if first_token_at is None:
                                first_token_at = time.monotonic()
                            token_parts.append(event["content"])
                        elif event["type"] == "tool_result":
                            tool_calls.append(
                                ToolCallTrace(
                                    tool_name=event["tool_name"],
                                    tool_kwargs={},
                                    output=event["output"],
                                    is_error=event["is_error"],
                                )
                            )
                        elif event["type"] == "done":
                            final_response = event["response"]
                            metrics["truncated"] = bool(event.get("truncated"))
                        elif event["type"] == "error":
                            # 同 /agent_chat_stream：错误发生时不能让"用拼接
                            # token 兜底"的逻辑往会话历史里写一条空 assistant
                            # 消息，见下面的说明。
                            errored = True
                        yield json.dumps(event, ensure_ascii=False) + "\n"
                except Exception as e:
                    error_logger.error(f"ask_stream agent branch error: {e}")
                    yield json.dumps(
                        {"type": "error", "message": "出错了，请稍后再试一下吧"}, ensure_ascii=False
                    ) + "\n"
                    return

                if not final_response:
                    final_response = "".join(token_parts)
                source_nodes = extract_source_nodes(tool_calls)
                metrics["tool_calls"] = len(tool_calls)
                metrics["retrieval_count"] = len(source_nodes)
            else:
                # standard 分支：复用 route_query 已经算好的 nodes + 压缩后的
                # query_str，不重复检索、不重复压缩问题——见
                # handlers/auto_router.py 模块 docstring"避免重复计算"一节。
                retriever = _PrefetchedNodesRetriever(decision.nodes)
                workflow = QAWorkflow(retriever=retriever, timeout=60)
                handler = workflow.run(
                    query=decision.query_str,
                    chat_history=history,
                    streaming=True,
                    skip_condense=True,
                )
                metrics["retrieval_count"] = len(decision.nodes or [])
                try:
                    async for ev in handler.stream_events():
                        if isinstance(ev, TokenEvent):
                            if first_token_at is None:
                                first_token_at = time.monotonic()
                            yield json.dumps({"type": "token", "content": ev.token}, ensure_ascii=False) + "\n"
                    result = await handler
                except Exception as e:
                    error_logger.error(f"ask_stream standard branch error: {e}")
                    yield json.dumps(
                        {"type": "error", "message": "出错了，请稍后再试一下吧"}, ensure_ascii=False
                    ) + "\n"
                    return

                final_response = result.response
                source_nodes = result.source_nodes
                yield json.dumps(
                    {"type": "done", "response": final_response, "tool_call_count": 0, "truncated": False},
                    ensure_ascii=False,
                ) + "\n"

            _last_query_response.set(client_id, source_nodes)
            if not errored and final_response.strip():
                history.append(ChatMessage(role=MessageRole.USER, content=query))
                history.append(ChatMessage(role=MessageRole.ASSISTANT, content=final_response))
                _chat_histories.set(client_id, history)

            metrics["answer_chars"] = len(final_response)
            metrics["est_completion_tokens"] = ai_metrics.estimate_tokens(final_response)

            # 成功回答后写入自动语义缓存（best-effort，store_auto 自己不抛异常）。
            # 下次问同样的问题直接命中，跳过检索 + LLM 生成。
            await qa_cache.store_auto(query, final_response, source_nodes)

            # 追问建议：必须在答案已经流式吐完之后才开始生成，不能拖慢用户看到
            # 答案的时间；生成函数自己已经把所有失败路径都降级成空列表，这里的
            # try/except 是双保险，防止调用方式本身出现意外（比如未来传参改动）
            # 导致这一步的异常反过来炸穿已经成功完成的主回答。
            try:
                suggestions = await generate_followup_suggestions(decision.query_str, source_nodes)
            except Exception:
                error_logger.error("ask_stream: 生成追问建议异常，降级为空列表", exc_info=True)
                suggestions = []
            yield json.dumps({"type": "suggestions", "suggestions": suggestions}, ensure_ascii=False) + "\n"
        finally:
            # 无论正常结束、出错 return、还是客户端断开（GeneratorExit），都把
            # 这条请求的指标落盘。断开时事件循环在关闭流程中，await 可能被拒，
            # 兜底 try/except 保证不炸。
            metrics["latency_ms"] = round((time.monotonic() - started) * 1000)
            if first_token_at is not None:
                metrics["first_token_ms"] = round((first_token_at - started) * 1000)
            try:
                await ai_metrics.log_request(**metrics)
            except Exception:
                pass

    return StreamingResponse(_event_gen(), media_type="application/x-ndjson")
