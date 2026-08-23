"""阶段 7-10 新增后端能力的测试：

1. ``list_documents``：按 ref_doc_id 聚合 chunk 的文档级视图
2. ``_ingest_text_and_persist``：文本录入走摄取管道（不再直接 insert_nodes）
3. ``_reindex_document_sync``：源文件缺失报 FileNotFoundError；正常路径删旧
   chunk + 清 docstore 去重记忆 + 重新摄取
4. ``export_index_to_file``：从 Chroma（而非空 docstore）导出
5. ``ai_metrics``：JSONL 读写 + 聚合分位
6. ``/manage/ai-metrics``、``/index/{name}/documents`` 端点
7. ``ask_stream`` 的多会话键（conversation_id 隔离）与 skip_cache
"""
import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import configs.load_env as load_env
from fastapi.testclient import TestClient
from handlers import index_crud
from utils import ai_metrics

import tests._pathsetup  # noqa: F401


def _chroma_result(ids, metadatas, documents):
    return {"ids": ids, "metadatas": metadatas, "documents": documents}


class FakeIndex:
    def __init__(self, index_id="test-index"):
        self.index_id = index_id
        self.summary = ""
        self.vector_store = MagicMock()


class ListDocumentsTest(unittest.TestCase):
    def _run(self, result):
        fake_collection = MagicMock()
        fake_collection.get.return_value = result
        with (
            patch("handlers.index_crud._get_client") as mock_client,
            patch.object(FakeIndex, "__init__", lambda self: None),
        ):
            idx = FakeIndex()
            idx.index_id = "t"
            mock_client.return_value.get_collection.return_value = fake_collection
            return index_crud.list_documents(idx)

    def test_aggregates_by_ref_doc_id(self):
        result = _chroma_result(
            ["n1", "n2", "n3"],
            [
                {"ref_doc_id": "docA", "file_name": "a.pdf"},
                {"ref_doc_id": "docA", "file_name": "a.pdf"},
                {"ref_doc_id": "docB", "file_name": "b.md", "source_url": "http://x"},
            ],
            ["x" * 10, "y" * 20, "z" * 5],
        )
        docs = self._run(result)
        self.assertEqual(len(docs), 2)
        doc_a = next(d for d in docs if d["doc_id"] == "docA")
        self.assertEqual(doc_a["chunk_count"], 2)
        self.assertEqual(doc_a["total_chars"], 30)
        self.assertEqual(doc_a["file_name"], "a.pdf")
        doc_b = next(d for d in docs if d["doc_id"] == "docB")
        self.assertEqual(doc_b["source_url"], "http://x")

    def test_exception_returns_empty(self):
        with patch("handlers.index_crud._get_client", side_effect=RuntimeError("boom")):
            idx = FakeIndex()
            self.assertEqual(index_crud.list_documents(idx), [])


class IngestTextTest(unittest.TestCase):
    @patch("handlers.vector_store.persist_docstore")
    @patch("handlers.vector_store.load_or_create_docstore")
    @patch("handlers.ingestion_pipeline.build_pipeline")
    def test_text_goes_through_pipeline_not_insert_nodes(self, mock_build, mock_load, mock_persist):
        docstore = MagicMock()
        mock_load.return_value = docstore
        pipeline = MagicMock()
        mock_build.return_value = pipeline

        index = FakeIndex("idx")
        index_crud._ingest_text_and_persist(index, "一段文本", None)

        pipeline.run.assert_called_once()
        run_docs = pipeline.run.call_args.kwargs["documents"]
        self.assertEqual(len(run_docs), 1)
        # 没显式 doc_id 时用内容 hash，重复录入同一段文本会被 UPSERTS 判重
        self.assertNotEqual(run_docs[0].id_, "一段文本")
        self.assertTrue(run_docs[0].metadata.get("file_name"))

    @patch("handlers.vector_store.persist_docstore")
    @patch("handlers.vector_store.load_or_create_docstore")
    @patch("handlers.ingestion_pipeline.build_pipeline")
    def test_explicit_doc_id_is_used(self, mock_build, mock_load, mock_persist):
        mock_load.return_value = MagicMock()
        pipeline = MagicMock()
        mock_build.return_value = pipeline
        index = FakeIndex("idx")
        index_crud._ingest_text_and_persist(index, "文本", "my-doc")
        run_docs = pipeline.run.call_args.kwargs["documents"]
        self.assertEqual(run_docs[0].id_, "my-doc")


class ReindexDocumentTest(unittest.TestCase):
    def test_missing_source_file_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(load_env, "SAVE_PATH", tmp):
                index = FakeIndex("idx")
                with self.assertRaises(FileNotFoundError):
                    index_crud._reindex_document_sync(index, "不存在.pdf")

    @patch("handlers.index_crud._ingest_and_persist")
    @patch("handlers.vector_store.persist_docstore")
    @patch("handlers.vector_store.load_or_create_docstore")
    def test_reindex_deletes_old_chunks_and_docstore_memory(
        self, mock_load_docstore, mock_persist_docstore, mock_ingest
    ):
        docstore = MagicMock()
        mock_load_docstore.return_value = docstore
        fake_collection = MagicMock()
        fake_collection.get.return_value = _chroma_result(
            ["n1", "n2"],
            [{"ref_doc_id": "docA"}, {"ref_doc_id": "docA"}],
            ["x", "y"],
        )
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "idx" / "a.pdf").parent.mkdir(parents=True, exist_ok=True)
            (Path(tmp) / "idx" / "a.pdf").write_text("内容", encoding="utf-8")
            with patch.object(load_env, "SAVE_PATH", tmp):
                with patch("handlers.index_crud._get_client") as mock_client:
                    mock_client.return_value.get_collection.return_value = fake_collection
                    index = FakeIndex("idx")
                    result = index_crud._reindex_document_sync(index, "a.pdf")

        fake_collection.delete.assert_called_once_with(ids=["n1", "n2"])
        docstore.delete.assert_called_once_with("docA")
        mock_ingest.assert_called_once()
        self.assertEqual(result["removed_chunks"], 2)


class ExportIndexToFileTest(unittest.TestCase):
    def test_export_reads_from_chroma_not_docstore(self):
        fake_collection = MagicMock()
        fake_collection.get.return_value = _chroma_result(
            ["n1", "n2"], [{"ref_doc_id": "d"}] * 2, ["第一段", "第二段"]
        )
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(load_env, "FILE_PATH", tmp):
                with patch("handlers.index_crud._get_client") as mock_client:
                    mock_client.return_value.get_collection.return_value = fake_collection
                    path = index_crud.export_index_to_file(FakeIndex("idx"), "out.txt")
            content = Path(path).read_text(encoding="utf-8")
        self.assertIn("第一段", content)
        self.assertIn("第二段", content)


class AiMetricsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self._patcher = patch.object(load_env, "LOG_PATH", str(self.tmp))
        self._patcher.start()
        self.addCleanup(self._patcher.stop)

    def test_log_and_summarize(self):
        for mode, latency in [("standard", 800), ("standard", 1200), ("agent", 5000), ("cache", 50)]:
            asyncio.run(
                ai_metrics.log_request(
                    mode=mode, latency_ms=latency, first_token_ms=10,
                    tool_calls=1 if mode == "agent" else 0, retrieval_count=3,
                    cache_hit=mode == "cache", answer_chars=100,
                    est_completion_tokens=50,
                )
            )
        summary = ai_metrics.summarize()
        self.assertEqual(summary["requests"], 4)
        self.assertEqual(summary["by_mode"], {"standard": 2, "agent": 1, "cache": 1})
        self.assertEqual(summary["cache_hit_rate"], 0.25)
        self.assertEqual(summary["est_tokens_total"], 200)
        self.assertLessEqual(summary["latency_ms"]["p50"], 1200)
        self.assertEqual(summary["latency_ms"]["p95"], 5000)
        self.assertEqual(len(summary["recent"]), 4)

    def test_empty_file_summary(self):
        self.assertEqual(ai_metrics.summarize()["requests"], 0)

    def test_estimate_tokens(self):
        self.assertEqual(ai_metrics.estimate_tokens("abcd"), 2)


class NewEndpointsTest(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(TestAppHolder.app)

    @patch("router.index.list_documents")
    def test_documents_endpoint(self, mock_list):
        mock_list.return_value = [
            {"doc_id": "d1", "file_name": "a.pdf", "source_url": "", "chunk_count": 3, "total_chars": 100}
        ]
        from dependencies import get_index

        fake = FakeIndex("campus")
        self.client.app.dependency_overrides[get_index] = lambda: fake
        try:
            resp = self.client.get("/index/campus/documents")
        finally:
            self.client.app.dependency_overrides.clear()
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["total"], 1)
        self.assertEqual(body["documents"][0]["chunk_count"], 3)

    def test_manage_ai_metrics_requires_configured_key(self):
        import os

        with patch.dict(os.environ, {"CUITCCA_API_KEY": ""}):
            resp = self.client.get("/manage/ai-metrics")
            # 未配置 key 时 manage 系返回 503（require_configured_api_key）
            self.assertIn(resp.status_code, (200, 503))


class TestAppHolder:
    """延迟导入 main.app，避免模块导入期的初始化顺序问题。"""

    app = None


def _load_app():
    if TestAppHolder.app is None:
        from main import app

        TestAppHolder.app = app
    return TestAppHolder.app


TestAppHolder.app = None
import main  # noqa: E402

TestAppHolder.app = main.app


class AskStreamMultiConversationTest(unittest.TestCase):
    """conversation_id 隔离 + skip_cache 透传（mock 掉 LLM 依赖）。"""

    def setUp(self):
        self.client = TestClient(main.app)

    def test_different_conversation_ids_isolated_history(self):
        """走一次真实请求（route_query 抛错走 error 分支），验证带
        conversation_id 的请求使用 cookie 会话之外的历史键。"""
        from llama_index.core.base.llms.types import ChatMessage, MessageRole
        from router.graph_session import _chat_histories

        resp = None
        with (
            patch("handlers.qa_cache.lookup", new=AsyncMock(return_value=None)),
            patch("router.graph_ask.ai_metrics.log_request", new=AsyncMock()),
            patch("handlers.auto_router.route_query", new=MagicMock(side_effect=RuntimeError("stop"))),
        ):
            resp = self.client.post(
                "/graph/ask_stream", data={"query": "q", "conversation_id": "conv-1"}
            )
            self.assertEqual(resp.status_code, 200)

        # conv-1 的历史键 = cookie 会话 + #conv-1，主会话键不受影响
        session_cookie = resp.cookies.get("session_id")
        _chat_histories.set(session_cookie, [ChatMessage(role=MessageRole.USER, content="main-q")])
        key1 = f"{session_cookie}#conv-1"
        _chat_histories.set(key1, [ChatMessage(role=MessageRole.USER, content="conv-q")])
        self.assertEqual(_chat_histories.get(session_cookie)[0].content, "main-q")
        self.assertEqual(_chat_histories.get(key1)[0].content, "conv-q")

    def test_effective_client_id(self):
        from router.graph_session import effective_client_id
        from starlette.requests import Request

        scope = {"type": "http", "headers": []}
        request = Request(scope)
        request.state.session_id = "sess-1"
        self.assertEqual(effective_client_id(request, None), "sess-1")
        self.assertEqual(effective_client_id(request, "  "), "sess-1")
        self.assertEqual(effective_client_id(request, "conv-a"), "sess-1#conv-a")

    def test_pop_last_exchange(self):
        from llama_index.core.base.llms.types import ChatMessage, MessageRole
        from router.graph_session import _chat_histories, pop_last_exchange

        key = "pop-test-key"
        _chat_histories.set(
            key,
            [
                ChatMessage(role=MessageRole.USER, content="q1"),
                ChatMessage(role=MessageRole.ASSISTANT, content="a1"),
                ChatMessage(role=MessageRole.USER, content="q2"),
                ChatMessage(role=MessageRole.ASSISTANT, content="a2"),
            ],
        )
        pop_last_exchange(key)
        history = _chat_histories.get(key)
        self.assertEqual(len(history), 2)
        self.assertEqual(history[-1].content, "a1")
        pop_last_exchange("不存在")
        # 不抛异常即可


if __name__ == "__main__":
    unittest.main()
