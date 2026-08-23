"""服务端会话历史的持久化与上限。

此前历史只在内存 TTLCache 里（200 会话 / 1 小时），进程一重启就清零，而前端
localStorage 那份还在——用户看着满屏对话，追问却拿不到指代对象（condense
静默丢上下文），反馈端点的防投毒校验也会跟着 400。这些用例锁住修复后的行为：

1. 写进去的历史落到 SQLite，进程重启（= 丢掉内存热层）后还能读回来；
2. 超过 ``CHAT_HISTORY_MAX_TURNS`` 的部分被截断，不会无限增长；
3. ``pop_last_exchange``（前端"重新生成"）同步反映到持久层；
4. 关掉 ``CHAT_HISTORY_PERSIST`` 时退化成纯内存，不碰数据库；
5. 保留期清理只删过期会话。
"""
import datetime as dt
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import configs.load_env as load_env
import utils.db as stats_db
from llama_index.core.base.llms.types import ChatMessage, MessageRole
from router import graph_session

import tests._pathsetup  # noqa: F401


def _msgs(*pairs) -> list[ChatMessage]:
    return [ChatMessage(role=role, content=content) for role, content in pairs]


class ChatHistoryPersistenceTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db_path = str(Path(self._tmp.name) / "app.db")
        stats_db.init_db(self.db_path)
        for attr, value in (
            ("db_path", self.db_path),
            ("CHAT_HISTORY_PERSIST", True),
            ("CHAT_HISTORY_MAX_TURNS", 3),
            ("CHAT_HISTORY_RETENTION_DAYS", 30),
        ):
            patcher = patch.object(load_env, attr, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.store = graph_session.PersistentChatHistory()

    def test_history_survives_process_restart(self):
        key = "session-1#conv-a"
        self.store.set(key, _msgs(
            (MessageRole.USER, "学校的校训是什么？"),
            (MessageRole.ASSISTANT, "成于大气 信达天下"),
        ))

        # 新实例 = 新进程：内存热层是空的，只能靠库里那份
        restarted = graph_session.PersistentChatHistory()
        history = restarted.get(key)

        self.assertIsNotNone(history)
        self.assertEqual([m.content for m in history],
                         ["学校的校训是什么？", "成于大气 信达天下"])
        self.assertEqual(history[0].role, MessageRole.USER)
        self.assertEqual(history[1].role, MessageRole.ASSISTANT)

    def test_history_is_capped_at_max_turns(self):
        key = "session-1#conv-b"
        long_history = []
        for i in range(10):
            long_history += _msgs(
                (MessageRole.USER, f"问题{i}"),
                (MessageRole.ASSISTANT, f"回答{i}"),
            )
        self.store.set(key, long_history)

        kept = graph_session.PersistentChatHistory().get(key)

        # CHAT_HISTORY_MAX_TURNS=3 -> 6 条消息，保留最近的三轮
        self.assertEqual(len(kept), 6)
        self.assertEqual(kept[0].content, "问题7")
        self.assertEqual(kept[-1].content, "回答9")

    def test_pop_last_exchange_is_persisted(self):
        key = "session-1#conv-c"
        with patch.object(graph_session, "_chat_histories", self.store):
            self.store.set(key, _msgs(
                (MessageRole.USER, "第一问"),
                (MessageRole.ASSISTANT, "第一答"),
                (MessageRole.USER, "第二问"),
                (MessageRole.ASSISTANT, "第二答"),
            ))
            graph_session.pop_last_exchange(key)

        kept = graph_session.PersistentChatHistory().get(key)
        self.assertEqual([m.content for m in kept], ["第一问", "第一答"])

    def test_persist_disabled_keeps_db_untouched(self):
        key = "session-1#conv-d"
        with patch.object(load_env, "CHAT_HISTORY_PERSIST", False):
            store = graph_session.PersistentChatHistory()
            store.set(key, _msgs((MessageRole.USER, "不该落库")))
            self.assertIsNotNone(store.get(key))  # 内存里还在
            self.assertIsNone(graph_session.PersistentChatHistory().get(key))

        with sqlite3.connect(self.db_path) as conn:
            count = conn.execute("SELECT COUNT(*) FROM chat_messages").fetchone()[0]
        self.assertEqual(count, 0)

    def test_prune_removes_only_expired_sessions(self):
        stats_db.replace_chat_history(self.db_path, "fresh", [("user", "新的")])
        stats_db.replace_chat_history(self.db_path, "stale", [("user", "旧的")])
        old = (dt.datetime.now(dt.UTC) - dt.timedelta(days=90)).isoformat(timespec="seconds")
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("UPDATE chat_messages SET updated_at = ? WHERE session_key = 'stale'", (old,))
            conn.commit()

        deleted = stats_db.prune_chat_history(self.db_path, 30)

        self.assertEqual(deleted, 1)
        self.assertEqual(stats_db.load_chat_history(self.db_path, "stale"), [])
        self.assertEqual(stats_db.load_chat_history(self.db_path, "fresh"), [("user", "新的")])

    def test_prune_disabled_with_non_positive_retention(self):
        stats_db.replace_chat_history(self.db_path, "keep", [("user", "留着")])
        self.assertEqual(stats_db.prune_chat_history(self.db_path, 0), 0)
        self.assertEqual(stats_db.load_chat_history(self.db_path, "keep"), [("user", "留着")])


if __name__ == "__main__":
    unittest.main()
