"""索引拓扑 A/B：多索引路由（现状） vs 单一合并索引。

## 要回答的问题

线上现在有多个知识库 collection（campus / campus-web / campus-corpus …），
``handlers/qa_workflow._build_retriever()`` 在索引数 > 1 时会构造
``RouterRetriever`` + ``LLMSingleSelector``：**每次提问先花一次 LLM 调用挑
出一个索引，然后只在那一个索引里检索**。代价有两层——多一次 LLM 往返，
以及"选错就彻底查不到"（正确文档在另一个索引里也没用）。

本脚本用同一份 golden 集，把两种拓扑放在完全相同的检索配置下对比：

- A 组 ``multi_index_router``：现状。``loadAllIndexes()`` 之后直接调用生产
  的 ``_build_retriever()``，索引集合就是线上真实加载的那几个。
- B 组 ``single_merged``：把各知识库的向量原样搬进一个合并 collection（不
  重新 embedding，见 ``build_merged_collection``），再走同一个
  ``build_retriever_for_index``。

两组都用生产的混合检索 + 条件重排（``utils.rerank.rerank_nodes``），唯一
变量是"多索引 + 路由"还是"单索引全量检索"。

## 额外指标：路由损失

A 组未命中、B 组命中的题目，说明正确文档在语料里、只是被路由挡在了另一个
索引外——这就是多索引拓扑独有的失败模式，单独统计成 ``routing_loss``。

用法::

    uv run python evals/run_index_topology_eval.py                 # 完整 A/B
    uv run python evals/run_index_topology_eval.py --rebuild-merged
    uv run python evals/run_index_topology_eval.py --top-k 5
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from datetime import datetime
from pathlib import Path

# 作为独立命令行工具直接跑（uv run python evals/xxx.py）时，仓库根不在
# sys.path 里，`import evals._common` 会失败——跟同目录其它评测脚本一样先补上。
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from evals._common import (  # noqa: E402
    EVALS_DIR,
    bootstrap_backend_path,
    first_hit_rank,
    format_retrieved,
    hit_rate_at,
    load_backend_env,
    load_jsonl,
    mrr_at,
)

DEFAULT_GOLDEN = EVALS_DIR / "golden.seed.jsonl"
DEFAULT_RESULTS_DIR = EVALS_DIR / "results"
DEFAULT_TOP_K = 5
MERGED_COLLECTION = "campus-merged-eval"


def build_merged_collection(source_names: list[str], target: str, rebuild: bool) -> tuple[int, int]:
    """把若干 collection 的向量搬进一个合并 collection（复用生产实现）。

    去重与搬运逻辑在 ``handlers.vector_store.merge_collections``——评测和
    ``scripts/merge_indexes.py`` 用同一份代码，避免"评测量的是 A、上线跑的
    是 B"这种最难查的偏差。
    """
    from handlers.vector_store import _get_client, get_or_create_collection, merge_collections

    client = _get_client()
    if target in [c.name for c in client.list_collections()]:
        if not rebuild:
            return get_or_create_collection(target).count(), 0
        client.delete_collection(target)

    result = merge_collections(source_names, target)
    return result["added"], result["skipped"]


def _retrieve_and_rerank(retriever, question: str, top_k: int):
    """返回 ``(nodes, 毫秒, 失败原因或 None)``。

    检索失败必须逐题吞掉而不是让整个评测炸穿：``RouterRetriever`` 的
    ``LLMSingleSelector`` 会解析不出选择（"Failed to select retriever"），这
    正是多索引拓扑要度量的失败模式之一——生产端在
    ``qa_workflow.retrieve`` 里同样是 try/except 兜住、降级成空结果，用户看到
    的是"我还不知道"。这里把它记成一次未命中并统计次数。
    """
    from llama_index.core.schema import QueryBundle
    from utils.rerank import rerank_nodes

    bundle = QueryBundle(query_str=question)
    started = time.perf_counter()
    try:
        nodes = retriever.retrieve(bundle)
        reranked, _ = rerank_nodes(nodes, bundle)
    except Exception as exc:  # noqa: BLE001 - 评测要的是"失败率"，不是堆栈
        return [], (time.perf_counter() - started) * 1000, f"{type(exc).__name__}: {exc}"
    elapsed_ms = (time.perf_counter() - started) * 1000
    return reranked[:top_k], elapsed_ms, None


def _evaluate(group: str, retriever, golden: list[dict], top_k: int) -> dict:
    details = []
    for item in golden:
        nodes, elapsed_ms, error = _retrieve_and_rerank(retriever, item["question"], top_k)
        expected = item.get("expected_sources") or []
        rank, matched = first_hit_rank(expected, nodes)
        details.append({
            "id": item["id"],
            "question": item["question"],
            "category": item.get("category", "uncategorized"),
            "expected_sources": expected,
            "hit": rank is not None,
            "rank": rank,
            "matched_source": matched,
            "latency_ms": round(elapsed_ms, 1),
            "error": error,
            "retrieved": format_retrieved(nodes),
        })
    ranks = [d["rank"] for d in details]
    latencies = sorted(d["latency_ms"] for d in details)
    return {
        "group": group,
        "count": len(details),
        "hit_rate": hit_rate_at(ranks, top_k),
        "mrr": mrr_at(ranks, top_k),
        "hit_at_1": sum(1 for r in ranks if r == 1) / len(ranks) if ranks else 0.0,
        "retrieval_errors": sum(1 for d in details if d["error"]),
        "latency_ms": {
            "mean": round(statistics.fmean(latencies), 1) if latencies else 0.0,
            "p50": round(statistics.median(latencies), 1) if latencies else 0.0,
            "p95": round(latencies[int(len(latencies) * 0.95) - 1], 1) if latencies else 0.0,
        },
        "details": details,
    }


def _print_group(result: dict) -> None:
    lat = result["latency_ms"]
    print(
        f"{result['group']:<20} hit@1={result['hit_at_1']:>7.2%}  "
        f"hit_rate={result['hit_rate']:>7.2%}  mrr={result['mrr']:.3f}  "
        f"latency mean={lat['mean']:.0f}ms p95={lat['p95']:.0f}ms  "
        f"检索失败={result['retrieval_errors']}"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="索引拓扑 A/B：多索引路由 vs 单一合并索引")
    parser.add_argument("--golden", type=Path, default=DEFAULT_GOLDEN)
    parser.add_argument("--top-k", type=int, default=DEFAULT_TOP_K)
    parser.add_argument("--merged-collection", default=MERGED_COLLECTION)
    parser.add_argument("--rebuild-merged", action="store_true", help="重建合并 collection")
    parser.add_argument(
        "--keep-merged",
        action="store_true",
        help="跑完保留评测用的合并 collection（默认删除——留着会在下次启动时被"
             "loadAllIndexes 当成知识库加载，把线上索引数又抬回多索引）",
    )
    parser.add_argument("--results-dir", type=Path, default=DEFAULT_RESULTS_DIR)
    args = parser.parse_args()

    load_backend_env()
    bootstrap_backend_path()

    import asyncio

    import configs.load_env as load_env
    from configs.llm_predictor import init_settings
    from handlers.hybrid_retriever import build_retriever_for_index, invalidate_hybrid_retriever_cache
    from handlers.index_crud import indexes, loadAllIndexes
    from handlers.qa_workflow import _build_retriever, resolve_effective_top_k
    from handlers.vector_store import build_index_from_collection, get_or_create_collection

    init_settings()
    golden = load_jsonl(args.golden)
    recall_k = resolve_effective_top_k(None)

    # ---- A 组：现状（多索引 + LLMSingleSelector 路由） ----
    asyncio.run(loadAllIndexes())
    # 把上一次跑留下的合并 collection 排掉：它们是本脚本自己的产物，留在候选里
    # 会同时污染两组（A 组多出一个路由候选、B 组把自己的输出再合并一遍）。
    live_indexes = [
        idx.index_id for idx in indexes if not idx.index_id.startswith(args.merged_collection)
    ]
    print(f"[topology] 线上加载的索引: {live_indexes}")
    if len(live_indexes) < 2:
        print("[topology] 当前只有一个索引，A 组无意义，退出。")
        return 1
    result_a = _evaluate("multi_index_router", _build_retriever(recall_k), golden, args.top_k)

    # ---- B 组：合并成单一索引 ----
    added, skipped = build_merged_collection(live_indexes, args.merged_collection, args.rebuild_merged)
    print(f"[topology] 合并 collection {args.merged_collection}: {added} chunk（去重跳过 {skipped}）")
    # 合并 collection 是删了重建的，而 build_retriever_for_index 按 index_id
    # 缓存 retriever——不清缓存的话，上一轮针对同名 collection 建好的 retriever
    # 会拿着已经不存在的 collection UUID 去查，整组 76 题全部 NotFoundError。
    invalidate_hybrid_retriever_cache()
    merged_index = build_index_from_collection(get_or_create_collection(args.merged_collection))
    merged_index.set_index_id(args.merged_collection)
    result_b = _evaluate("single_merged", build_retriever_for_index(merged_index, recall_k), golden, args.top_k)

    # ---- C 组：合并索引 + 加宽召回 ----
    # 合并后候选池比任何单个索引都大，recall_k 沿用单索引时代的值（20）相当于
    # 变相收紧了召回；rerank 前多给一倍候选，看质量差距是不是就是这么丢的。
    wide_k = recall_k * 2
    result_c = _evaluate(
        f"single_merged_recall{wide_k}",
        build_retriever_for_index(merged_index, wide_k),
        golden,
        args.top_k,
    )

    # ---- D 组：合并索引但不含爬取的网页 ----
    # golden 集的期望来源全是上传语料的文件名，campus-web 的 1092 个网页 chunk
    # 对这些题目是纯噪声。分出来一组，才能区分"合并本身有代价"和"合并进来的
    # 那批内容跟评测集不相干"。
    no_web = [n for n in live_indexes if n != "campus-web"]
    no_web_collection = f"{args.merged_collection}-noweb"
    added_d, skipped_d = build_merged_collection(no_web, no_web_collection, args.rebuild_merged)
    print(f"[topology] 合并（不含网页）{no_web_collection}: {added_d} chunk（去重跳过 {skipped_d}）")
    invalidate_hybrid_retriever_cache()
    no_web_index = build_index_from_collection(get_or_create_collection(no_web_collection))
    no_web_index.set_index_id(no_web_collection)
    result_d = _evaluate(
        "single_merged_no_web",
        build_retriever_for_index(no_web_index, recall_k),
        golden,
        args.top_k,
    )

    # ---- 路由损失：A 未命中而 B 命中的题 ----
    b_by_id = {d["id"]: d for d in result_b["details"]}
    routing_loss = [
        {"id": d["id"], "question": d["question"], "expected_sources": d["expected_sources"]}
        for d in result_a["details"]
        if not d["hit"] and b_by_id.get(d["id"], {}).get("hit")
    ]

    print()
    for result in (result_a, result_b, result_c, result_d):
        _print_group(result)
    print(f"\n路由损失（A 漏 / B 中）: {len(routing_loss)} 题")
    for item in routing_loss:
        print(f"  - {item['id']} {item['question']}  期望来源={item['expected_sources']}")

    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "golden": str(args.golden),
        "top_k": args.top_k,
        "recall_k": recall_k,
        "rerank_enabled": load_env.RERANK_ENABLED,
        "hybrid_enabled": load_env.HYBRID_RETRIEVAL_ENABLED,
        "live_indexes": live_indexes,
        "merged_collection": args.merged_collection,
        "groups": [result_a, result_b, result_c, result_d],
        "routing_loss": routing_loss,
    }
    args.results_dir.mkdir(parents=True, exist_ok=True)
    out = args.results_dir / f"index_topology_{datetime.now():%Y%m%d_%H%M%S}.json"
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n结果已写入 {out}")

    if not args.keep_merged:
        from handlers.vector_store import _get_client

        client = _get_client()
        for name in (args.merged_collection, no_web_collection):
            if name in [c.name for c in client.list_collections()]:
                client.delete_collection(name)
        print("[topology] 已清理评测用的合并 collection（--keep-merged 可保留）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
