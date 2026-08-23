import unittest  # noqa: I001 (tests._pathsetup must precede main below)
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient

import tests._pathsetup  # noqa: F401
from main import app


class GraphRouterTest(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def tearDown(self):
        app.dependency_overrides.clear()

    # ── /graph/create ──────────────────────────────────────────────

    def test_create(self):
        response = self.client.post("/graph/create")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})

        from router.graph import _chat_histories
        client_id = response.cookies.get("session_id")
        self.assertEqual(_chat_histories.get(client_id), [])

    # ── /graph/query ───────────────────────────────────────────────

    @patch("handlers.qa_workflow.QAWorkflow")
    def test_query_success(self, mock_workflow_cls):
        from handlers.qa_workflow import QAWorkflowResult

        mock_instance = MagicMock()
        mock_instance.run = AsyncMock(
            return_value=QAWorkflowResult(response="answer text", source_nodes=[])
        )
        mock_workflow_cls.return_value = mock_instance

        response = self.client.post("/graph/query", data={"query": "what is this?"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"response": "answer text"})

    @patch("handlers.qa_workflow.QAWorkflow")
    def test_query_empty_response(self, mock_workflow_cls):
        # QAWorkflow.synthesize returns the fallback text itself when no nodes
        # are retrieved (see handlers/qa_workflow.py _FALLBACK_ANSWER) — the
        # router no longer does its own "Empty Response" -> fallback text
        # translation, it just passes QAWorkflowResult.response through.
        from handlers.qa_workflow import _FALLBACK_ANSWER, QAWorkflowResult

        mock_instance = MagicMock()
        mock_instance.run = AsyncMock(
            return_value=QAWorkflowResult(response=_FALLBACK_ANSWER, source_nodes=[])
        )
        mock_workflow_cls.return_value = mock_instance

        response = self.client.post("/graph/query", data={"query": "unknown"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"response": "我还不知道，请反馈给我吧"})

    @patch("handlers.qa_workflow.QAWorkflow")
    def test_query_engine_raises(self, mock_workflow_cls):
        mock_workflow_cls.side_effect = ValueError("broken")

        response = self.client.post("/graph/query", data={"query": "hello"})

        self.assertEqual(response.status_code, 500)
        self.assertEqual(
            response.json(),
            {"status": "detail", "message": "出错了，请稍后在试一下吧"},
        )

    @patch("handlers.qa_workflow.QAWorkflow")
    def test_query_aquery_raises(self, mock_workflow_cls):
        mock_instance = MagicMock()
        mock_instance.run = AsyncMock(side_effect=RuntimeError("query failed"))
        mock_workflow_cls.return_value = mock_instance

        response = self.client.post("/graph/query", data={"query": "hello"})

        self.assertEqual(response.status_code, 500)
        self.assertEqual(
            response.json(),
            {"status": "detail", "message": "出错了，请稍后在试一下吧"},
        )

    # ── /graph/query_stream ───────────────────────────────────────

    @patch("handlers.qa_workflow.QAWorkflow")
    def test_query_stream(self, mock_workflow_cls):
        from handlers.qa_workflow import QAWorkflowResult, TokenEvent

        class FakeHandler:
            async def stream_events(self):
                for tok in ["chunk1", "chunk2"]:
                    yield TokenEvent(token=tok)

            def __await__(self):
                async def _result():
                    return QAWorkflowResult(response="chunk1chunk2", source_nodes=[])
                return _result().__await__()

        mock_instance = MagicMock()
        mock_instance.run = MagicMock(return_value=FakeHandler())
        mock_workflow_cls.return_value = mock_instance

        response = self.client.post("/graph/query_stream", data={"query": "hello"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.text, "chunk1chunk2")
        # Regression: this endpoint must run the workflow in streaming mode
        # (streaming=True), otherwise in production (unmocked) synthesize()
        # would use the non-streaming achat() path and TokenEvents would
        # never be emitted for this endpoint to consume.
        mock_instance.run.assert_called_once()
        _, kwargs = mock_instance.run.call_args
        self.assertEqual(kwargs.get("streaming"), True)

    @patch("handlers.qa_workflow.QAWorkflow")
    def test_query_stream_falls_back_to_error_message_on_exception(self, mock_workflow_cls):
        from handlers.qa_workflow import TokenEvent

        class FakeHandler:
            async def stream_events(self):
                yield TokenEvent(token="部分")
                raise RuntimeError("boom")

            def __await__(self):
                async def _result():
                    raise RuntimeError("boom")
                return _result().__await__()

        mock_instance = MagicMock()
        mock_instance.run = MagicMock(return_value=FakeHandler())
        mock_workflow_cls.return_value = mock_instance

        response = self.client.post("/graph/query_stream", data={"query": "会出错的问题"})

        self.assertEqual(response.status_code, 200)
        self.assertIn("出错了，请稍后在试一下吧", response.text)

    # ── /graph/query_sources ──────────────────────────────────────

    @patch("handlers.qa_workflow.QAWorkflow")
    def test_query_sources_returns_sources(self, mock_workflow_cls):
        from handlers.qa_workflow import QAWorkflowResult

        source_node = MagicMock()
        source_node.node.id_ = "n1"
        source_node.node.text = "some text"
        source_node.score = 0.95

        mock_instance = MagicMock()
        mock_instance.run = AsyncMock(
            return_value=QAWorkflowResult(response="answer", source_nodes=[source_node])
        )
        mock_workflow_cls.return_value = mock_instance

        self.client.post("/graph/query", data={"query": "q"})
        response = self.client.post("/graph/query_sources")

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(len(data["source_nodes"]), 1)
        self.assertEqual(data["source_nodes"][0]["id"], "n1")
        self.assertEqual(data["source_nodes"][0]["text"], "some text")
        self.assertEqual(data["source_nodes"][0]["score"], 0.95)

    def test_query_sources_uses_conversation_scoped_key(self):
        """多会话下取来源必须用 {session}#{conversation} 复合键。

        ``/ask_stream`` 就是按这个键存来源的（前端每次请求都带 conversation_id）。
        ``/query_sources`` 如果只按 cookie 取，取到的永远是空——"参考来源"面板
        在多会话上线之后对所有会话都是空的，而且不报错，只是静默什么都不显示。
        """
        from llama_index.core.schema import NodeWithScore, TextNode
        from router.graph_session import _last_query_response, effective_client_id

        class _FakeRequest:
            def __init__(self, session_id):
                self.state = type("S", (), {"session_id": session_id})()
                self.cookies = {"session_id": session_id}

        # 先让客户端拿到一个 session cookie，再按"这个 cookie + 会话 id"存来源
        self.client.post("/graph/create")
        session_id = self.client.cookies.get("session_id")
        key = effective_client_id(_FakeRequest(session_id), "conv-42")
        _last_query_response.set(key, [
            NodeWithScore(node=TextNode(text="来自 conv-42 的依据", id_="n42"), score=0.8),
        ])

        matched = self.client.post("/graph/query_sources", data={"conversation_id": "conv-42"})
        self.assertEqual(matched.status_code, 200)
        self.assertEqual(matched.json()["source_nodes"][0]["id"], "n42")

        # 另一个会话 id 不该串到这条来源上
        other = self.client.post("/graph/query_sources", data={"conversation_id": "conv-99"})
        self.assertEqual(other.status_code, 400)

    def test_query_sources_no_prior_query(self):
        response = self.client.post("/graph/query_sources")

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.json(),
            {"status": "detail", "message": "please query first"},
        )

    # ── /graph/agent ───────────────────────────────────────────────

    @patch("handlers.qa_workflow.QAWorkflow")
    def test_agent(self, mock_workflow_cls):
        from handlers.qa_workflow import QAWorkflowResult

        mock_instance = MagicMock()
        mock_instance.run = AsyncMock(
            return_value=QAWorkflowResult(response="agent answer", source_nodes=[])
        )
        mock_workflow_cls.return_value = mock_instance

        response = self.client.post("/graph/agent", data={"query": "hello"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"response": "agent answer"})

    @patch("handlers.qa_workflow.QAWorkflow")
    def test_agent_aquery_raises(self, mock_workflow_cls):
        mock_instance = MagicMock()
        mock_instance.run = AsyncMock(side_effect=RuntimeError("query fail"))
        mock_workflow_cls.return_value = mock_instance

        response = self.client.post("/graph/agent", data={"query": "hello"})

        self.assertEqual(response.status_code, 500)
        self.assertEqual(
            response.json(),
            {"status": "detail", "message": "出错了，请稍后在试一下吧"},
        )

    # ── /graph/query_history ──────────────────────────────────────

    def test_query_history_success(self):
        response = self.client.post("/graph/create")
        response = self.client.post("/graph/query_history")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"history": []})

    def test_query_history_not_found(self):
        response = self.client.post("/graph/query_history")

        self.assertEqual(response.status_code, 404)
        self.assertEqual(
            response.json(),
            {"status": "detail", "message": "No query graph available"},
        )

    # ── /graph/query_router ───────────────────────────────────────

    @patch("handlers.qa_workflow.QAWorkflow")
    def test_query_router(self, mock_workflow_cls):
        from handlers.qa_workflow import QAWorkflowResult

        mock_instance = MagicMock()
        mock_instance.run = AsyncMock(
            return_value=QAWorkflowResult(response="router response", source_nodes=[])
        )
        mock_workflow_cls.return_value = mock_instance

        response = self.client.post("/graph/query_router", data={"query": "hello"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"response": "router response"})
        mock_instance.run.assert_awaited_once()

    # ── /graph/chat_stream ────────────────────────────────────────
    #
    # /chat_stream now runs QAWorkflow(streaming=True) and consumes
    # TokenEvents via handler.stream_events(), same protocol as
    # /workflow_query_stream — see tests/test_qa_workflow_router.py for the
    # FakeHandler pattern this mirrors.

    @patch("handlers.qa_workflow.QAWorkflow")
    def test_chat_stream_creates_engine_if_missing(self, mock_workflow_cls):
        from handlers.qa_workflow import QAWorkflowResult, TokenEvent

        class FakeHandler:
            async def stream_events(self):
                for tok in ["chat", "response"]:
                    yield TokenEvent(token=tok)

            def __await__(self):
                async def _result():
                    return QAWorkflowResult(response="chatresponse", source_nodes=[])
                return _result().__await__()

        mock_instance = MagicMock()
        mock_instance.run = MagicMock(return_value=FakeHandler())
        mock_workflow_cls.return_value = mock_instance

        response = self.client.post("/graph/chat_stream", data={"query": "hi"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.text, "chatresponse")

    @patch("handlers.qa_workflow.QAWorkflow")
    def test_chat_stream_uses_existing_engine(self, mock_workflow_cls):
        from handlers.qa_workflow import QAWorkflowResult, TokenEvent

        class FakeHandler:
            async def stream_events(self):
                yield TokenEvent(token="existing")

            def __await__(self):
                async def _result():
                    return QAWorkflowResult(response="existing", source_nodes=[])
                return _result().__await__()

        mock_instance = MagicMock()
        mock_instance.run = MagicMock(return_value=FakeHandler())
        mock_workflow_cls.return_value = mock_instance

        self.client.post("/graph/create")
        response = self.client.post("/graph/chat_stream", data={"query": "hi"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.text, "existing")

    @patch("handlers.qa_workflow.QAWorkflow")
    def test_chat_stream_populates_query_sources(self, mock_workflow_cls):
        from handlers.qa_workflow import QAWorkflowResult, TokenEvent

        source_node = MagicMock()
        source_node.node.id_ = "n1"
        source_node.node.text = "cited text"
        source_node.score = 0.8

        class FakeHandler:
            async def stream_events(self):
                yield TokenEvent(token="hello")

            def __await__(self):
                async def _result():
                    return QAWorkflowResult(response="hello", source_nodes=[source_node])
                return _result().__await__()

        mock_instance = MagicMock()
        mock_instance.run = MagicMock(return_value=FakeHandler())
        mock_workflow_cls.return_value = mock_instance

        self.client.post("/graph/chat_stream", data={"query": "hi"})
        response = self.client.post("/graph/query_sources")

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(len(data["source_nodes"]), 1)
        self.assertEqual(data["source_nodes"][0]["id"], "n1")

    @patch("handlers.qa_workflow.QAWorkflow")
    def test_chat_stream_falls_back_to_error_message_on_exception(self, mock_workflow_cls):
        from handlers.qa_workflow import TokenEvent

        class FakeHandler:
            async def stream_events(self):
                yield TokenEvent(token="部分")
                raise RuntimeError("boom")

            def __await__(self):
                async def _result():
                    raise RuntimeError("boom")
                return _result().__await__()

        mock_instance = MagicMock()
        mock_instance.run = MagicMock(return_value=FakeHandler())
        mock_workflow_cls.return_value = mock_instance

        response = self.client.post("/graph/chat_stream", data={"query": "会出错的问题"})

        self.assertEqual(response.status_code, 200)
        self.assertIn("出错了，请稍后在试一下吧", response.text)


class WebsocketQueryEndpointTest(unittest.TestCase):
    """websocket /graph/query 之前完全没有测试，本次切换到 QAWorkflow 时补上。

    覆盖：未配置 CUITCCA_API_KEY 时拒绝连接、token 不对拒绝连接、正常收发一条
    消息、workflow 抛异常时返回兜底文案。鉴权逻辑本身未改动。
    """

    def setUp(self):
        self.client = TestClient(app)

    def tearDown(self):
        app.dependency_overrides.clear()

    def test_rejects_connection_when_api_key_not_configured(self):
        with patch.dict("os.environ", {"CUITCCA_API_KEY": ""}):
            with self.assertRaises(Exception):
                with self.client.websocket_connect("/graph/query"):
                    pass

    def test_rejects_connection_with_wrong_token(self):
        with patch.dict("os.environ", {"CUITCCA_API_KEY": "correct-key"}):
            with self.assertRaises(Exception):
                with self.client.websocket_connect("/graph/query?token=wrong-token"):
                    pass

    @patch("handlers.qa_workflow.QAWorkflow")
    def test_sends_and_receives_one_message(self, mock_workflow_cls):
        from handlers.qa_workflow import QAWorkflowResult

        mock_instance = MagicMock()
        mock_instance.run = AsyncMock(
            return_value=QAWorkflowResult(response="ws answer", source_nodes=[])
        )
        mock_workflow_cls.return_value = mock_instance

        with patch.dict("os.environ", {"CUITCCA_API_KEY": "correct-key"}):
            with self.client.websocket_connect("/graph/query?token=correct-key") as websocket:
                websocket.send_text("hello")
                data = websocket.receive_text()

        self.assertEqual(data, "ws answer")

    @patch("handlers.qa_workflow.QAWorkflow")
    def test_returns_fallback_message_on_workflow_exception(self, mock_workflow_cls):
        mock_instance = MagicMock()
        mock_instance.run = AsyncMock(side_effect=RuntimeError("boom"))
        mock_workflow_cls.return_value = mock_instance

        with patch.dict("os.environ", {"CUITCCA_API_KEY": "correct-key"}):
            with self.client.websocket_connect("/graph/query?token=correct-key") as websocket:
                websocket.send_text("hello")
                data = websocket.receive_text()

        self.assertEqual(data, "出错了，请稍后在试一下吧")


if __name__ == "__main__":
    unittest.main()
