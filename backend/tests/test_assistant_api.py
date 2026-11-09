"""Exercise the HTTP boundary without network calls or background ingestion."""
import asyncio
import copy
import json
import sqlite3
import unittest
from unittest.mock import patch, AsyncMock, Mock
from backend.app.query_understanding_service import empty_understanding

from backend.app import main, rag_service
from backend.app.embedding_service import EmbeddingProviderError
from backend.app.llm_service import LLMProviderError
from test_rag import FakeLLM, retrieval_result


async def request(path, payload):
    messages = []
    async def receive():
        return {"type": "http.request", "body": json.dumps(payload).encode(), "more_body": False}
    async def send(message):
        messages.append(message)
    await main.app({"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
                    "method": "POST", "scheme": "http", "path": path, "raw_path": path.encode(),
                    "query_string": b"", "root_path": "", "headers": [(b"content-type", b"application/json")],
                    "server": ("test", 80), "client": ("test", 1)}, receive, send)
    status = next(m["status"] for m in messages if m["type"] == "http.response.start")
    body = b"".join(m.get("body", b"") for m in messages if m["type"] == "http.response.body")
    return status, json.loads(body)


class AssistantApiTests(unittest.TestCase):
    def setUp(self):
        self.provider = FakeLLM()
        self.original = rag_service.answer_question
        self.search = patch.object(rag_service.retrieval_service, "retrieve_evidence", return_value=copy.deepcopy(retrieval_result())).start()
        self.service = patch.object(rag_service, "answer_question", side_effect=lambda cid, q: self.original(cid, q, llm_provider=self.provider)).start()
        self.addCleanup(patch.stopall)
        store = Mock()
        store.load_course.return_value = {"course_id": 42}
        patch.object(rag_service, "default_store", return_value=store).start()
        patch.object(rag_service.query_understanding_service, "understand_question",
                     new=AsyncMock(return_value=empty_understanding("Explain FCFS"))).start()

    def ask(self, payload=None, course="42"):
        return asyncio.run(request(f"/api/courses/{course}/assistant", {"question": "Explain FCFS"} if payload is None else payload))

    def test_answered_and_source_citation_preservation(self):
        code, body = self.ask()
        self.assertEqual(code, 200)
        self.assertEqual(body["status"], "answered")
        self.assertEqual(body["answer_blocks"][0]["source_ids"], ["S1"])
        self.assertEqual(body["sources"][0]["id"], "S1")
        self.assertEqual(body["sources"][0]["origins"], retrieval_result()["results"][0]["sources"])
        self.assertEqual(body["retrieval_status"], "ready")
        self.assertIn("warnings", body)
        self.service.assert_called_once_with(42, "Explain FCFS")

    def test_insufficient_context(self):
        self.provider.answer = {"status": "insufficient_context", "blocks": [], "limitations": ["No supporting material."]}
        self.assertEqual(self.ask()[1]["status"], "insufficient_context")

    def test_structured_data_needed(self):
        self.provider.answer = {"status": "needs_structured_data", "blocks": [], "limitations": []}
        code, body = self.ask()
        self.assertEqual(code, 200)
        self.assertEqual(body["status"], "needs_structured_data")
        self.assertEqual(body["sources"], [])

    def test_indexing_needed(self):
        self.search.return_value = {"status": "not_indexed", "results": []}
        self.assertEqual(self.ask()[1]["status"], "indexing_needed")
        self.assertEqual(self.provider.calls, [])

    def test_invalid_course(self):
        for cid in ("0", "-1", "abc"):
            with self.subTest(cid=cid):
                self.assertEqual(self.ask(course=cid)[0], 422)
        self.service.assert_not_called()

    def test_invalid_question(self):
        for payload in ({}, {"question": ""}, {"question": "  "}, {"question": 5}, {"question": None}, {"question": "é" * 4001}):
            with self.subTest(payload_type=type(payload.get("question"))):
                self.assertEqual(self.ask(payload)[0], 422)
        self.service.assert_not_called()

    def test_missing_saved_course_is_safe(self):
        self.service.side_effect = ValueError("private configuration")
        code, body = self.ask()
        self.assertEqual(code, 422)
        self.assertNotIn("private", json.dumps(body))

    def test_provider_failures_are_safe(self):
        for error in (LLMProviderError("secret upstream body"), EmbeddingProviderError("secret API key")):
            self.service.side_effect = error
            code, body = self.ask()
            self.assertEqual(code, 503)
            self.assertNotIn("secret", json.dumps(body))

    def test_invalid_citations_are_not_returned(self):
        self.provider.answer["blocks"][0]["source_ids"] = ["S999"]
        code, body = self.ask()
        self.assertEqual(code, 502)
        self.assertNotIn("S999", json.dumps(body))

    def test_storage_and_unexpected_failures_are_safe(self):
        for error, expected in ((sqlite3.DatabaseError("private path"), 503), (RuntimeError("secret key"), 500)):
            self.service.side_effect = error
            code, body = self.ask()
            self.assertEqual(code, expected)
            self.assertNotIn(str(error), json.dumps(body))
