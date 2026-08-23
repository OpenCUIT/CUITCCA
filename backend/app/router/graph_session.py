"""graph 路由的会话状态：按 client_id（= 会话 cookie 的 session_id）隔离的
聊天历史与最近一次来源节点。

独立成模块的原因：graph.py 拆成 qa/agent/ask/feedback 四个子路由后，这些
状态是四组端点唯一的共享物（写历史的和读历史的在不同端点），必须放在大家
都能 import 的地方而不是任何一方的模块里。TTLCache 的容量/过期参数原来写
死在 graph.py 顶部，原样搬过来。
"""
import asyncio
import logging
import time
from collections import OrderedDict

import configs.load_env as load_env
import utils.db as stats_db
from fastapi import Request
from llama_index.core.base.llms.types import ChatMessage, MessageRole

logger = logging.getLogger(__name__)

# 会话缓存最大容量
_MAX_SESSIONS = 200
_SESSION_TTL = 3600  # 1小时


class TTLCache:
    """简单的 TTL + LRU 缓存，替代裸 dict"""

    def __init__(self, max_size: int = _MAX_SESSIONS, ttl: int = _SESSION_TTL):
        self._data: OrderedDict = OrderedDict()
        self._max_size = max_size
        self._ttl = ttl

    def get(self, key):
        entry = self._data.get(key)
        if entry is None:
            return None
        if time.time() - entry[1] > self._ttl:
            self._data.pop(key, None)
            return None
        self._data.move_to_end(key)
        return entry[0]

    def set(self, key, value):
        self._data[key] = (value, time.time())
        self._data.move_to_end(key)
        while len(self._data) > self._max_size:
            self._data.popitem(last=False)

    def __contains__(self, key):
        return self.get(key) is not None

    def __len__(self):
        return len(self._data)


def _client_id(request: Request) -> str:
    if hasattr(request.state, "session_id"):
        return request.state.session_id
    return request.cookies.get("session_id") or "unknown"


def effective_client_id(request: Request, conversation_id: str | None) -> str:
    """多会话支持的会话键：同一浏览器 cookie 下用 ``{session}#{conversation}``
    隔离出多份独立的服务端历史。

    前端每个会话生成一个 uuid 作为 conversation_id 随请求带上；不带时行为
    与原来完全一致（cookie 会话即历史），旧端点/旧测试零影响。反馈端点
    （/graph/qa_feedback）也要用同一个键才能通过防投毒校验。
    """
    base = _client_id(request)
    conversation_id = (conversation_id or "").strip()
    if not conversation_id:
        return base
    return f"{base}#{conversation_id[:64]}"


def pop_last_exchange(key: str) -> None:
    """弹掉会话里最后一轮 user+assistant 问答（前端"重新生成"时调用：
    先撤回上一轮，再用同一个问题重跑，condense 才不会把"刚才已经答过
    一遍"带进上下文）。"""
    history = _chat_histories.get(key)
    if not history or len(history) < 2:
        return
    _chat_histories.set(key, history[:-2])


class PersistentChatHistory:
    """内存热层 + SQLite 持久层的会话历史。

    对外沿用 ``TTLCache`` 的 ``get``/``set`` 接口（四个 graph 子路由都在用），
    区别只在两头：

    - ``get()`` 内存 miss 时回源 SQLite。此前历史只在内存里，进程一重启就
      清零，而前端 localStorage 那份还在——用户看着满屏对话，追问"它是哪一
      年成立的"却拿不到指代对象，condense 静默丢上下文；反馈端点的防投毒
      校验也会跟着 400。守护脚本崩溃即重启，这个窗口并不罕见。
    - ``set()`` 先落内存再把写库丢进线程池。SQLite 写是同步阻塞调用，压在
      事件循环上就是这个项目在 index 路由里刚修过的那个坑；历史写失败不该
      让一次提问失败，所以是 best-effort + 日志。

    同时按 ``CHAT_HISTORY_MAX_TURNS`` 截断：不设上限的话长对话会一路把
    condense 的 prompt 撑大，成本和延迟跟着涨，最后撞上下文窗口。
    """

    def __init__(self) -> None:
        self._hot = TTLCache()

    def _max_messages(self) -> int:
        return max(2, load_env.CHAT_HISTORY_MAX_TURNS * 2)

    def get(self, key):
        cached = self._hot.get(key)
        if cached is not None:
            return cached
        if not load_env.CHAT_HISTORY_PERSIST:
            return None
        try:
            rows = stats_db.load_chat_history(load_env.db_path, key, self._max_messages())
        except Exception:
            logger.warning("读取持久化会话历史失败，降级为空历史。", exc_info=True)
            return None
        if not rows:
            return None
        history = [
            ChatMessage(role=MessageRole(role), content=content) for role, content in rows
        ]
        self._hot.set(key, history)
        return history

    def set(self, key, history) -> None:
        trimmed = list(history)[-self._max_messages():]
        self._hot.set(key, trimmed)
        self._persist(key, trimmed)

    def _persist(self, key, history) -> None:
        if not load_env.CHAT_HISTORY_PERSIST:
            return
        payload = [
            (str(getattr(m.role, "value", m.role)), m.content or "") for m in history
        ]
        db_path = load_env.db_path

        def _write() -> None:
            try:
                stats_db.replace_chat_history(db_path, key, payload)
            except Exception:
                logger.warning("持久化会话历史失败（不影响本次回答）。", exc_info=True)

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            _write()  # 同步上下文（测试、脚本）直接写
        else:
            loop.run_in_executor(None, _write)

    def clear(self) -> None:
        """清空内存热层（测试隔离用；不动持久层，那是用户数据）。"""
        self._hot._data.clear()

    def hot_values(self):
        """热层里当前缓存的各会话历史。只读用途（测试断言"历史没被污染"），
        不代表持久层的全量内容。"""
        return [entry[0] for entry in self._hot._data.values()]

    def __contains__(self, key) -> bool:
        return self.get(key) is not None

    def __len__(self) -> int:
        return len(self._hot)


_chat_histories: PersistentChatHistory = PersistentChatHistory()
# 来源节点仍然只在内存：NodeWithScore 带 embedding 和全文，落库成本远高于
# 收益，而它的用途（答完立刻拉引用来源、反馈时附带来源）都是短窗口行为。
# 代价是重启后点赞入 curated 缓存的条目会没有来源，可接受。
_last_query_response: TTLCache = TTLCache()
