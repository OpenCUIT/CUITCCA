"""结构化校园数据层（utils/campus_data.py）+ 种子脚本（scripts/seed_campus_data.py）
的单元测试。

全部跑在临时 SQLite / 临时语料目录上，不碰 data/app.db 与 data/corpus。

覆盖：
1. 查询层：分页/倒序、关键词 LIKE、分类过滤、facet 计数、LIKE 通配符转义、
   with_content 开关、services 关键词命中（keywords 字段别名）
2. 种子脚本：front-matter 解析、非新闻栏目过滤、噪声行清洗、摘要生成、
   UPSERT 幂等（重复灌不换 id 不重复行）
3. Agent 工具：search_announcements / search_campus_services 走临时库返回
   结构化 JSON，空结果如实返回 0
"""
import asyncio
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import configs.load_env as load_env

import tests._pathsetup  # noqa: F401

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "scripts"))

from seed_campus_data import parse_corpus, seed  # noqa: E402
from utils import campus_data  # noqa: E402
from utils.db import init_db  # noqa: E402


def _make_corpus(tmpdir: Path) -> None:
    dated = (
        "---\n"
        "source_url: https://www.cuit.edu.cn/info/1002/1.htm\n"
        "title: 关于期末考试安排的通知\n"
        "publish_date: '2026-07-01'\n"
        "category: 通知公告\n"
        "site: main\n"
        "content_hash: hash-aaa\n"
        "---\n"
        "# 关于期末考试安排的通知\n\n"
        "<!-- 注：本页正文经通用兜底规则提取 -->\n"
        "- 当前位置: 首页 >> 通知公告\n"
        "\n各 位 同学：\n\n期末考试定于 7 月 10 日开始。\n"
    )
    static_page = (
        "---\n"
        "source_url: https://gjjl.cuit.edu.cn/gatsw.htm\n"
        "title: 港澳台事务\n"
        "publish_date: null\n"
        "category: 港澳台事务\n"
        "site: gjjl\n"
        "content_hash: hash-bbb\n"
        "---\n"
        "# 港澳台事务\n\n常驻介绍页，不该进通知表。\n"
    )
    news = (
        "---\n"
        "source_url: https://www.cuit.edu.cn/info/1005/2.htm\n"
        "title: 学校召开教学工作会\n"
        "publish_date: '2026-08-01'\n"
        "category: 成信要闻\n"
        "site: main\n"
        "content_hash: hash-ccc\n"
        "---\n"
        "# 学校召开教学工作会\n\n8 月 1 日，学校在航空港校区召开教学工作会。\n"
    )
    (tmpdir / "a__hashaaa.md").write_text(dated, encoding="utf-8")
    (tmpdir / "b__hashbbb.md").write_text(static_page, encoding="utf-8")
    (tmpdir / "c__hashccc.md").write_text(news, encoding="utf-8")


class CampusDataQueryTest(unittest.TestCase):
    def setUp(self):
        import tempfile

        self.tmp = Path(tempfile.mkdtemp())
        self.db = str(self.tmp / "campus_test.db")
        init_db(self.db)
        corpus_dir = self.tmp / "corpus"
        corpus_dir.mkdir()
        _make_corpus(corpus_dir)
        services_json = self.tmp / "services.json"
        services_json.write_text(
            json.dumps(
                [
                    {
                        "name": "校车交通",
                        "category": "生活服务",
                        "icon": "🚌",
                        "keywords": "校车 班车 通勤",
                        "content": "校车时刻表……",
                        "source": "校车时刻表.txt",
                    }
                ],
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        seed(self.db, corpus_dir, services_json)

    def test_parse_corpus_filters_non_news(self):
        corpus_dir = self.tmp / "corpus"
        rows = parse_corpus(corpus_dir)
        titles = [r["title"] for r in rows]
        # 常驻介绍页（无 publish_date、非新闻栏目）不进通知表
        self.assertEqual(sorted(titles), ["关于期末考试安排的通知", "学校召开教学工作会"])
        exam = next(r for r in rows if "期末考试" in r["title"])
        self.assertEqual(exam["category"], "通知公告")
        self.assertEqual(exam["source"], "学校官网")
        # 爬取噪声（HTML 注释、面包屑）不进正文
        self.assertNotIn("当前位置", exam["content"])
        self.assertNotIn("<!--", exam["content"])
        self.assertIn("期末考试定于", exam["content"])
        self.assertTrue(exam["summary"])

    def test_list_announcements_order_and_pagination(self):
        result = campus_data.list_announcements(self.db, limit=1, offset=0)
        self.assertEqual(result["total"], 2)
        # 按发布时间倒序：8 月的教学工作会排前面
        self.assertEqual(result["items"][0]["title"], "学校召开教学工作会")
        # 列表默认不带正文
        self.assertNotIn("content", result["items"][0])
        page2 = campus_data.list_announcements(self.db, limit=1, offset=1)
        self.assertEqual(page2["items"][0]["title"], "关于期末考试安排的通知")

    def test_search_and_category_filter(self):
        result = campus_data.list_announcements(self.db, query="期末考试")
        self.assertEqual(result["total"], 1)
        self.assertIn("期末考试", result["items"][0]["title"])
        result = campus_data.list_announcements(self.db, category="成信要闻")
        self.assertEqual(result["total"], 1)

    def test_like_wildcards_escaped(self):
        # 查询词里的 % 不被当通配符（应命中 0 条而不是全部）
        result = campus_data.list_announcements(self.db, query="%")
        self.assertEqual(result["total"], 0)

    def test_with_content_flag(self):
        result = campus_data.list_announcements(self.db, with_content=True)
        self.assertIn("content", result["items"][0])

    def test_categories_facet(self):
        cats = campus_data.list_announcement_categories(self.db)
        self.assertEqual({c["category"] for c in cats}, {"通知公告", "成信要闻"})

    def test_services_keyword_search(self):
        # keywords 别名命中："班车" 不在 name/content 里，但在 keywords 里
        self.assertEqual(len(campus_data.list_campus_services(self.db, query="班车")), 1)
        self.assertEqual(len(campus_data.list_campus_services(self.db, query="不存在的服务")), 0)
        self.assertEqual(campus_data.get_campus_service(self.db, 1)["name"], "校车交通")

    def test_stats(self):
        stats = campus_data.campus_data_stats(self.db)
        self.assertEqual(stats["announcements"], 2)
        self.assertEqual(stats["campus_services"], 1)
        self.assertEqual(stats["latest_published_at"], "2026-08-01")

    def test_seed_idempotent(self):
        corpus_dir = self.tmp / "corpus"
        services_json = self.tmp / "services.json"
        first_id = campus_data.list_announcements(self.db)["items"][0]["id"]
        seed(self.db, corpus_dir, services_json)
        stats = campus_data.campus_data_stats(self.db)
        self.assertEqual(stats["announcements"], 2)
        self.assertEqual(stats["campus_services"], 1)
        self.assertEqual(campus_data.list_announcements(self.db)["items"][0]["id"], first_id)


class CampusAgentToolsTest(unittest.TestCase):
    """agents.tools 的新工具直接查临时库（patch db_path），验证 JSON 形状。"""

    def setUp(self):
        import tempfile

        from agents import tools as agent_tools

        self.tools_mod = agent_tools
        self.tmp = Path(tempfile.mkdtemp())
        self.db = str(self.tmp / "tools_test.db")
        init_db(self.db)
        with _connect_db(self.db) as conn:
            conn.execute(
                "INSERT INTO announcements (title, category, source, published_at, summary, content, content_hash)"
                " VALUES ('转专业通知', '通知公告', '教务处', '2026-06-01', '摘要', '正文内容', 'h1')"
            )
            conn.execute(
                "INSERT INTO campus_services (name, category, icon, keywords, content, source)"
                " VALUES ('学生公寓热水', '生活服务', '♨️', '热水 洗澡', '分时段供水……', '公寓热水.txt')"
            )
            conn.commit()

    def test_search_announcements_tool(self):
        with patch.object(load_env, "db_path", self.db):
            result = json.loads(asyncio.run(self.tools_mod.search_announcements(query="转专业")))
        self.assertEqual(result["total_matched"], 1)
        self.assertEqual(result["items"][0]["title"], "转专业通知")
        self.assertEqual(result["items"][0]["content"], "正文内容")

    def test_search_announcements_latest_without_query(self):
        with patch.object(load_env, "db_path", self.db):
            result = json.loads(asyncio.run(self.tools_mod.search_announcements()))
        self.assertEqual(result["returned"], 1)

    def test_search_campus_services_tool(self):
        with patch.object(load_env, "db_path", self.db):
            result = json.loads(asyncio.run(self.tools_mod.search_campus_services(query="洗澡")))
        self.assertEqual(len(result["items"]), 1)
        self.assertEqual(result["items"][0]["name"], "学生公寓热水")

    def test_new_tools_registered_in_default_registry(self):
        registry = self.tools_mod.build_default_registry()
        names = registry.list_names()
        self.assertIn("search_announcements", names)
        self.assertIn("search_campus_services", names)


def _connect_db(db_path: str):
    from utils.db import _connect

    return _connect(db_path)


if __name__ == "__main__":
    unittest.main()
