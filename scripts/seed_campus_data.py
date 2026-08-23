"""把爬取语料与人工整理的种子灌进 SQLite 的结构化校园数据表。

产出两张表（schema 见 backend/app/utils/db.py）：
- announcements：data/corpus/web/*.md 里带 publish_date 的新闻/通知页
  （524 条，通知公告/成信要闻/综合新闻/成信学术四个栏目）；
- campus_services：scripts/campus_services.json 里人工整理的校园服务
  （内容取自 信息搜集汇总/ 的真实文件）。

幂等：announcements 按 content_hash UPSERT，campus_services 按 name
UPSERT，重复执行不产生重复行、不换 id。

使用:
    uv run python scripts/seed_campus_data.py            # 默认灌 data/app.db
    uv run python scripts/seed_campus_data.py --dry-run  # 只统计不写库
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import yaml

_REPO_ROOT = Path(__file__).resolve().parent.parent
BACKEND_APP_DIR = _REPO_ROOT / "backend" / "app"
if str(BACKEND_APP_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_APP_DIR))

from configs.load_env import db_path as default_db_path  # noqa: E402
from utils.db import _connect, init_db  # noqa: E402

# 只收"按时间发布"的栏目页；其余（学校概况/部门概况/港澳台事务等）是常驻
# 介绍页，没有发布日期，不属于通知公告数据。
_NEWS_CATEGORIES = {"通知公告", "成信要闻", "综合新闻", "成信学术"}
_SITE_SOURCE = {
    "main": "学校官网",
    "jwc": "教务处",
    "xyw": "校友工作网",
    "gjjl": "国际交流处",
}

_FRONT_MATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n?", re.S)
# 正文里的爬取噪声行：面包屑导航、提取器兜底注释、空表格
_NOISE_LINE_RE = re.compile(
    r"^(<!--.*-->|- 当前位置.*|\|\s*\|?[-: ]+\|?\s*|\|  \|.*|####?\s*$)\s*$"
)


def _clean_body(body: str) -> str:
    lines = []
    for line in body.splitlines():
        if _NOISE_LINE_RE.match(line.strip()):
            continue
        lines.append(line.rstrip())
    # 压掉 3 连以上空行
    text = re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()
    return text


def _make_summary(body: str, max_chars: int = 160) -> str:
    plain = re.sub(r"[#*`>\-|]|^\s*$", "", body, flags=re.M)
    plain = re.sub(r"\s+", " ", plain).strip()
    return plain[:max_chars] + ("…" if len(plain) > max_chars else "")


def parse_corpus(corpus_dir: Path) -> list[dict]:
    rows: list[dict] = []
    for path in sorted(corpus_dir.glob("*.md")):
        text = path.read_text(encoding="utf-8", errors="replace")
        m = _FRONT_MATTER_RE.match(text)
        if not m:
            continue
        try:
            fm = yaml.safe_load(m.group(1)) or {}
        except yaml.YAMLError:
            continue
        if not isinstance(fm, dict):
            continue
        category = (fm.get("category") or "").strip()
        published_at = fm.get("publish_date")
        if category not in _NEWS_CATEGORIES or not published_at:
            continue
        body = _clean_body(text[m.end():])
        if not body:
            continue
        rows.append(
            {
                "title": (fm.get("title") or path.stem).strip(),
                "category": category,
                "source": _SITE_SOURCE.get(fm.get("site"), "学校官网"),
                "source_url": (fm.get("source_url") or "").strip() or None,
                "published_at": str(published_at),
                "summary": _make_summary(body),
                "content": body,
                "content_hash": fm.get("content_hash") or path.stem,
            }
        )
    return rows


def seed(db_file: str, corpus_dir: Path, services_json: Path, dry_run: bool = False) -> dict:
    announcements = parse_corpus(corpus_dir)
    services = yaml.safe_load(services_json.read_text(encoding="utf-8"))
    if not isinstance(services, list):
        raise SystemExit(f"campus_services.json 格式错误：应为列表，得到 {type(services).__name__}")

    counts = {"announcements": len(announcements), "campus_services": len(services)}
    if dry_run:
        return counts

    init_db(db_file)
    with _connect(db_file) as conn:
        conn.executemany(
            """
            INSERT INTO announcements
                (title, category, source, source_url, published_at, summary, content, content_hash)
            VALUES (:title, :category, :source, :source_url, :published_at, :summary, :content, :content_hash)
            ON CONFLICT(content_hash) DO UPDATE SET
                title=excluded.title, category=excluded.category, source=excluded.source,
                source_url=excluded.source_url, published_at=excluded.published_at,
                summary=excluded.summary, content=excluded.content
            """,
            announcements,
        )
        conn.executemany(
            """
            INSERT INTO campus_services (name, category, icon, keywords, content, source)
            VALUES (:name, :category, :icon, :keywords, :content, :source)
            ON CONFLICT(name) DO UPDATE SET
                category=excluded.category, icon=excluded.icon, keywords=excluded.keywords,
                content=excluded.content, source=excluded.source,
                updated_at=datetime('now')
            """,
            services,
        )
        conn.commit()
    return counts


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=default_db_path, help=f"SQLite 路径（默认 {default_db_path}）")
    parser.add_argument(
        "--corpus", default=str(_REPO_ROOT / "data" / "corpus" / "web"), help="爬取语料目录"
    )
    parser.add_argument(
        "--services-json",
        default=str(_REPO_ROOT / "scripts" / "campus_services.json"),
        help="校园服务种子 JSON",
    )
    parser.add_argument("--dry-run", action="store_true", help="只解析统计，不写库")
    args = parser.parse_args()

    counts = seed(Path(args.db), Path(args.corpus), Path(args.services_json), dry_run=args.dry_run)
    action = "解析到" if args.dry_run else "已写入"
    print(
        f"[seed] {action} 通知公告 {counts['announcements']} 条、"
        f"校园服务 {counts['campus_services']} 项 -> {args.db}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
