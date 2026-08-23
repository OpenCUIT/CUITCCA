"""结构化校园数据（通知公告 / 校园服务）的查询层。

跟 ChromaDB 知识库是两套数据形态，刻意分开：
- 知识库（向量 chunk）回答"XX 规定是什么"这类**语义**问题；
- 这里（SQLite 表）回答"最近有什么通知""校车几点"这类**列表/精确**问题——
  向量检索天然不擅长按时间排序的枚举查询，而 SQL 一条 ORDER BY 就够。

数据来源：scripts/seed_campus_data.py 把爬取语料（带 front-matter 的
markdown）转成 announcements 行；campus_services 来自人工整理的种子
（scripts/campus_services.json，内容取自真实语料文件）。

所有函数都是同步的（SQLite 本地读，毫秒级）：路由层负责用
asyncio.to_thread 卸载，跟 utils/db.py 的统计查询同一个约定。
"""
from __future__ import annotations

import sqlite3
from contextlib import closing

from utils.db import _connect

# LIKE 检索的转义字符。SQLite LIKE 默认只认 % 和 _（大小写不敏感对 ASCII
# 生效，中文无大小写问题），把用户输入里的这两个字符转义掉防止通配符注入
# 改变语义——虽然无安全风险（参数化查询），但"查询词里的 % 被当通配符"
# 会让搜索结果莫名其妙。
_LIKE_ESCAPE = "\\"


def _escape_like(term: str) -> str:
    return term.replace(_LIKE_ESCAPE, _LIKE_ESCAPE * 2).replace("%", r"\%").replace("_", r"\_")


def _row_to_announcement(row: sqlite3.Row, *, with_content: bool = True) -> dict:
    item = {
        "id": row["id"],
        "title": row["title"],
        "category": row["category"],
        "source": row["source"],
        "source_url": row["source_url"],
        "published_at": row["published_at"],
        "summary": row["summary"],
    }
    if with_content:
        item["content"] = row["content"]
    return item


def _announcement_where(query: str | None, category: str | None) -> tuple[str, list]:
    """组装 WHERE 子句。query 匹配标题/摘要/正文（LIKE），category 精确匹配。"""
    clauses, params = [], []
    if query:
        pattern = f"%{_escape_like(query)}%"
        clauses.append("(title LIKE ? ESCAPE '\\' OR summary LIKE ? ESCAPE '\\' OR content LIKE ? ESCAPE '\\')")
        params.extend([pattern, pattern, pattern])
    if category:
        clauses.append("category = ?")
        params.append(category)
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    return where, params


def list_announcements(
    db_path: str,
    *,
    query: str | None = None,
    category: str | None = None,
    limit: int = 20,
    offset: int = 0,
    with_content: bool = False,
) -> dict:
    """分页列出通知公告（按发布时间倒序）。返回 {total, items}，列表页只需
    要摘要（with_content=False）；Agent 工具要能直接答"这条通知说了什么"，
    传 with_content=True 把正文带上。"""
    limit = max(1, min(int(limit), 50))
    offset = max(0, int(offset))
    where, params = _announcement_where(query, category)
    with closing(_connect(db_path)) as conn:
        total = conn.execute(f"SELECT COUNT(*) FROM announcements{where}", params).fetchone()[0]
        rows = conn.execute(
            f"SELECT * FROM announcements{where} ORDER BY published_at DESC, id DESC LIMIT ? OFFSET ?",
            [*params, limit, offset],
        ).fetchall()
    return {
        "total": total,
        "items": [_row_to_announcement(r, with_content=with_content) for r in rows],
    }


def get_announcement(db_path: str, ann_id: int) -> dict | None:
    with closing(_connect(db_path)) as conn:
        row = conn.execute("SELECT * FROM announcements WHERE id = ?", (int(ann_id),)).fetchone()
    return _row_to_announcement(row) if row else None


def list_announcement_categories(db_path: str) -> list[dict]:
    """分类 facet：[{category, count}]，按数量倒序。"""
    with closing(_connect(db_path)) as conn:
        rows = conn.execute(
            "SELECT category, COUNT(*) AS count FROM announcements GROUP BY category ORDER BY count DESC"
        ).fetchall()
    return [{"category": r["category"], "count": r["count"]} for r in rows]


def _row_to_service(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "name": row["name"],
        "category": row["category"],
        "icon": row["icon"],
        "content": row["content"],
        "source": row["source"],
        "updated_at": row["updated_at"],
    }


def _service_where(query: str | None, category: str | None) -> tuple[str, list]:
    clauses, params = [], []
    if query:
        pattern = f"%{_escape_like(query)}%"
        # keywords 字段是种子数据里人工写的检索别名（如"热水 洗澡 水卡"），
        # 让"洗澡"也能命中"学生公寓热水"这条服务。
        clauses.append("(name LIKE ? ESCAPE '\\' OR keywords LIKE ? ESCAPE '\\' OR content LIKE ? ESCAPE '\\')")
        params.extend([pattern, pattern, pattern])
    if category:
        clauses.append("category = ?")
        params.append(category)
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    return where, params


def list_campus_services(
    db_path: str, *, query: str | None = None, category: str | None = None
) -> list[dict]:
    """列出校园服务（数量少，不分页）。"""
    where, params = _service_where(query, category)
    with closing(_connect(db_path)) as conn:
        rows = conn.execute(
            f"SELECT * FROM campus_services{where} ORDER BY category, id", params
        ).fetchall()
    return [_row_to_service(r) for r in rows]


def get_campus_service(db_path: str, service_id: int) -> dict | None:
    with closing(_connect(db_path)) as conn:
        row = conn.execute("SELECT * FROM campus_services WHERE id = ?", (int(service_id),)).fetchone()
    return _row_to_service(row) if row else None


def list_service_categories(db_path: str) -> list[dict]:
    with closing(_connect(db_path)) as conn:
        rows = conn.execute(
            "SELECT category, COUNT(*) AS count FROM campus_services GROUP BY category ORDER BY count DESC"
        ).fetchall()
    return [{"category": r["category"], "count": r["count"]} for r in rows]


def campus_data_stats(db_path: str) -> dict:
    """知识库 Dashboard / 首页统计用的总量。"""
    with closing(_connect(db_path)) as conn:
        ann = conn.execute("SELECT COUNT(*) FROM announcements").fetchone()[0]
        svc = conn.execute("SELECT COUNT(*) FROM campus_services").fetchone()[0]
        latest = conn.execute(
            "SELECT MAX(published_at) FROM announcements"
        ).fetchone()[0]
    return {"announcements": ann, "campus_services": svc, "latest_published_at": latest}
