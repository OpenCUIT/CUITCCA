import datetime as _dt
import sqlite3
from contextlib import closing

_SCHEMA = """
CREATE TABLE IF NOT EXISTS access_stats (
    key TEXT PRIMARY KEY,
    value INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS ip_visits (
    ip TEXT PRIMARY KEY,
    count INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS endpoint_visits (
    endpoint TEXT PRIMARY KEY,
    count INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS feedback (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    client_ip TEXT NOT NULL,
    email TEXT,
    message TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS chat_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_key TEXT NOT NULL,
    seq INTEGER NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_chat_messages_key ON chat_messages(session_key, seq);
CREATE INDEX IF NOT EXISTS idx_chat_messages_updated ON chat_messages(updated_at);
CREATE TABLE IF NOT EXISTS announcements (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT '通知公告',
    source TEXT NOT NULL DEFAULT '',
    source_url TEXT,
    published_at TEXT,
    summary TEXT NOT NULL DEFAULT '',
    content TEXT NOT NULL DEFAULT '',
    content_hash TEXT UNIQUE,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_announcements_category ON announcements(category);
CREATE INDEX IF NOT EXISTS idx_announcements_published ON announcements(published_at);
CREATE TABLE IF NOT EXISTS campus_services (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    category TEXT NOT NULL,
    icon TEXT NOT NULL DEFAULT '',
    keywords TEXT NOT NULL DEFAULT '',
    content TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_campus_services_category ON campus_services(category);
"""


def _connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA cache_size=-64000")
    conn.execute("PRAGMA temp_store=MEMORY")
    return conn


def init_db(db_path: str) -> None:
    with closing(_connect(db_path)) as conn:
        conn.executescript(_SCHEMA)
        conn.commit()


def flush_stats(db_path: str, stats: dict) -> None:
    with closing(_connect(db_path)) as conn:
        conn.execute(
            "INSERT INTO access_stats (key, value) VALUES ('total_visits', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (stats.get('total_visits', 0),),
        )
        user_visits = dict(stats.get('user_visits', {}))
        endpoint_visits = dict(stats.get('endpoint_visits', {}))
        if user_visits:
            conn.executemany(
                "INSERT INTO ip_visits (ip, count) VALUES (?, ?) "
                "ON CONFLICT(ip) DO UPDATE SET count = excluded.count",
                list(user_visits.items()),
            )
        if endpoint_visits:
            conn.executemany(
                "INSERT INTO endpoint_visits (endpoint, count) VALUES (?, ?) "
                "ON CONFLICT(endpoint) DO UPDATE SET count = excluded.count",
                list(endpoint_visits.items()),
            )
        conn.commit()


def record_visit(db_path: str, client_ip: str, endpoint: str) -> None:
    with closing(_connect(db_path)) as conn:
        conn.execute(
            "INSERT INTO ip_visits (ip, count) VALUES (?, 1) "
            "ON CONFLICT(ip) DO UPDATE SET count = count + 1",
            (client_ip,),
        )
        conn.execute(
            "INSERT INTO endpoint_visits (endpoint, count) VALUES (?, 1) "
            "ON CONFLICT(endpoint) DO UPDATE SET count = count + 1",
            (endpoint,),
        )
        conn.commit()


def load_stats(db_path: str) -> dict:
    with closing(_connect(db_path)) as conn:
        total_row = conn.execute(
            "SELECT value FROM access_stats WHERE key = 'total_visits'"
        ).fetchone()
        total_visits = total_row['value'] if total_row else 0
        user_visits = {
            row['ip']: row['count']
            for row in conn.execute("SELECT ip, count FROM ip_visits").fetchall()
        }
        endpoint_visits = {
            row['endpoint']: row['count']
            for row in conn.execute("SELECT endpoint, count FROM endpoint_visits").fetchall()
        }
    return {
        'total_visits': total_visits,
        'user_visits': user_visits,
        'endpoint_visits': endpoint_visits,
    }


def save_feedback(db_path: str, client_ip: str, email: str | None, message: str) -> None:
    with closing(_connect(db_path)) as conn:
        conn.execute(
            "INSERT INTO feedback (client_ip, email, message) VALUES (?, ?, ?)",
            (client_ip, email, message),
        )
        conn.commit()


def list_feedback(db_path: str, limit: int = 100) -> list[dict]:
    with closing(_connect(db_path)) as conn:
        rows = conn.execute(
            "SELECT created_at, client_ip, email, message FROM feedback "
            "ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(row) for row in rows]


# ===== 会话历史持久化 =====
# 服务端历史此前只在内存 TTLCache 里（200 会话 / 1 小时）。进程一重启就清零，
# 而前端 localStorage 那份还在——用户看着满屏对话，追问"它是哪一年成立的"却
# 拿不到指代对象，问题压缩（condense）静默失去上下文；反馈端点的防投毒校验
# （response 必须等于本会话最后一条 assistant 消息）也会因此 400。守护脚本
# 崩溃即重启，这个窗口比想象中常见。落到 SQLite（与统计/反馈同一个库、同一
# 套 WAL 配置）之后，内存 cache 退化成热层。


def replace_chat_history(db_path: str, session_key: str, messages: list[tuple[str, str]]) -> None:
    """整体替换一个会话键的历史（``(role, content)`` 有序列表）。

    整体替换而不是追加：调用方（router/graph_*）本来就是"读出整段 -> 追加
    两条 -> 写回整段"的用法，``pop_last_exchange``（重新生成）还会往回删，
    整体替换语义最直白，也不会出现内存与库不一致的中间态。
    """
    now = _dt.datetime.now(_dt.UTC).isoformat(timespec="seconds")
    with closing(_connect(db_path)) as conn:
        conn.execute("DELETE FROM chat_messages WHERE session_key = ?", (session_key,))
        if messages:
            conn.executemany(
                "INSERT INTO chat_messages (session_key, seq, role, content, updated_at) "
                "VALUES (?, ?, ?, ?, ?)",
                [(session_key, i, role, content, now) for i, (role, content) in enumerate(messages)],
            )
        conn.commit()


def load_chat_history(db_path: str, session_key: str, limit: int = 40) -> list[tuple[str, str]]:
    """读回一个会话键的历史，最多 ``limit`` 条（按写入顺序）。"""
    with closing(_connect(db_path)) as conn:
        rows = conn.execute(
            "SELECT role, content FROM chat_messages WHERE session_key = ? "
            "ORDER BY seq DESC LIMIT ?",
            (session_key, limit),
        ).fetchall()
    return [(r["role"], r["content"]) for r in reversed(rows)]


def prune_chat_history(db_path: str, retention_days: int) -> int:
    """删掉 ``retention_days`` 天没更新过的会话历史，返回删除条数。

    ``retention_days <= 0`` 表示不清理（留给"我就是要永久保留"的部署）。
    """
    if retention_days <= 0:
        return 0
    cutoff = (_dt.datetime.now(_dt.UTC) - _dt.timedelta(days=retention_days)).isoformat(
        timespec="seconds"
    )
    with closing(_connect(db_path)) as conn:
        cursor = conn.execute("DELETE FROM chat_messages WHERE updated_at < ?", (cutoff,))
        conn.commit()
        return cursor.rowcount or 0
