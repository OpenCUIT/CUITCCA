"""graph 路由的会话状态：按 client_id（= 会话 cookie 的 session_id）隔离的
聊天历史与最近一次来源节点。

独立成模块的原因：graph.py 拆成 qa/agent/ask/feedback 四个子路由后，这些
状态是四组端点唯一的共享物（写历史的和读历史的在不同端点），必须放在大家
都能 import 的地方而不是任何一方的模块里。TTLCache 的容量/过期参数原来写
死在 graph.py 顶部，原样搬过来。
"""
import time
from collections import OrderedDict

from fastapi import Request

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


_chat_histories: TTLCache = TTLCache()
_last_query_response: TTLCache = TTLCache()
