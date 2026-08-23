"""router/campus.py 的 /campus/* 端点测试。

用临时 SQLite + patch load_env.db_path，不碰 data/app.db；conftest 已把
CUITCCA_API_KEY 清空（autouse monkeypatch），端点处于无鉴权的开放形态。

覆盖：列表/搜索/分类/详情/404、服务列表与详情、stats、非法分页参数 422、
以及配了 API key 时的 401（鉴权依赖挂在路由上）。
"""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import configs.load_env as load_env
from fastapi.testclient import TestClient
from main import app
from utils.db import _connect, init_db

import tests._pathsetup  # noqa: F401


class CampusRouterTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.db = str(self.tmp / "router_test.db")
        init_db(self.db)
        with _connect(self.db) as conn:
            ann_columns = (
                "title, category, source, source_url, published_at, summary, content, content_hash"
            )
            conn.execute(
                f"INSERT INTO announcements ({ann_columns})"
                " VALUES ('评教通知', '通知公告', '教务处', 'https://jwc.cuit.edu.cn/1',"
                " '2026-07-01', '摘要', '正文', 'h1')"
            )
            conn.execute(
                "INSERT INTO announcements (title, category, source, published_at, summary, content, content_hash)"
                " VALUES ('开学新闻', '综合新闻', '学校官网', '2026-08-01', '摘要', '正文', 'h2')"
            )
            conn.execute(
                "INSERT INTO campus_services (name, category, icon, keywords, content, source)"
                " VALUES ('快递收发', '生活服务', '📦', '快递 菜鸟', '菜鸟驿站……', '快递.txt')"
            )
            conn.commit()
        env_patcher = patch.object(load_env, "db_path", self.db)
        env_patcher.start()
        self.addCleanup(env_patcher.stop)
        self.client = TestClient(app)

    def test_list_announcements_sorted_desc(self):
        resp = self.client.get("/campus/announcements")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["total"], 2)
        # 8 月的新闻排在 7 月的通知前面
        self.assertEqual(body["items"][0]["title"], "开学新闻")
        # 列表不带正文
        self.assertNotIn("content", body["items"][0])

    def test_search_and_category(self):
        self.assertEqual(self.client.get("/campus/announcements?query=评教").json()["total"], 1)
        self.assertEqual(
            self.client.get("/campus/announcements?category=综合新闻").json()["total"], 1
        )

    def test_detail_and_404(self):
        detail = self.client.get("/campus/announcements/1").json()
        self.assertEqual(detail["title"], "评教通知")
        self.assertEqual(detail["content"], "正文")
        self.assertEqual(detail["source_url"], "https://jwc.cuit.edu.cn/1")
        self.assertEqual(self.client.get("/campus/announcements/9999").status_code, 404)

    def test_categories(self):
        cats = self.client.get("/campus/announcements/categories").json()
        self.assertEqual({c["category"] for c in cats}, {"通知公告", "综合新闻"})

    def test_services_endpoints(self):
        body = self.client.get("/campus/services").json()
        self.assertEqual(body["total"], 1)
        self.assertEqual(body["items"][0]["name"], "快递收发")
        self.assertEqual(self.client.get("/campus/services/1").json()["name"], "快递收发")
        self.assertEqual(self.client.get("/campus/services/999").status_code, 404)
        self.assertEqual(len(self.client.get("/campus/services?query=菜鸟").json()["items"]), 1)

    def test_stats(self):
        stats = self.client.get("/campus/stats").json()
        self.assertEqual(stats["announcements"], 2)
        self.assertEqual(stats["campus_services"], 1)

    def test_invalid_pagination_422(self):
        self.assertEqual(self.client.get("/campus/announcements?limit=0").status_code, 422)
        self.assertEqual(self.client.get("/campus/announcements?limit=51").status_code, 422)

    def test_api_key_enforced_when_configured(self):
        import os

        with patch.dict(os.environ, {"CUITCCA_API_KEY": "secret"}):
            resp = self.client.get("/campus/announcements")
            self.assertEqual(resp.status_code, 401)
            resp = self.client.get(
                "/campus/announcements", headers={"Authorization": "Bearer secret"}
            )
            self.assertEqual(resp.status_code, 200)


if __name__ == "__main__":
    unittest.main()
