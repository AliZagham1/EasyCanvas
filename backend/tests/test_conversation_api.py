"""Exercise the streaming HTTP adapter with fake providers and no API credits."""
import asyncio
import json
import unittest
import uuid
from unittest.mock import Mock, patch

from backend.app import main, conversation_service, retrieval_service
from backend.tests.test_conversation import FakeConversationProvider
from backend.tests.test_rag import retrieval_result


async def request(payload, course="42"):
    messages, received = [], False
    async def receive():
        nonlocal received
        if not received:
            received = True
            return {"type": "http.request", "body": json.dumps(payload).encode(), "more_body": False}
        await asyncio.Event().wait()
    async def send(message): messages.append(message)
    path = f"/api/courses/{course}/assistant/stream"
    await main.app({"type": "http", "asgi": {"version": "3.0", "spec_version": "2.4"}, "http_version": "1.1", "method": "POST",
                    "scheme": "http", "path": path, "raw_path": path.encode(), "query_string": b"", "root_path": "",
                    "headers": [(b"content-type", b"application/json")], "server": ("test", 80), "client": ("test", 1)}, receive, send)
    status = next(m["status"] for m in messages if m["type"] == "http.response.start")
    text = b"".join(m.get("body", b"") for m in messages if m["type"] == "http.response.body").decode()
    events = []
    if status == 200:
        for frame in text.strip().split("\n\n"):
            if frame.startswith("event:"):
                kind, data = frame.split("\ndata: ", 1)
                events.append({"event": kind[7:], "data": json.loads(data)})
    return status, events or json.loads(text)


class ConversationApiTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.sessions = conversation_service.ConversationService()
        self.provider = FakeConversationProvider()
        store = Mock(namespace="student")
        store.load_documents.return_value = []
        store.load_course.return_value = {"course": {"metadata": {}}}
        patch.object(main, "conversations", self.sessions).start()
        patch.object(main, "get_cache_namespace", return_value="student").start()
        patch.object(conversation_service, "default_store", return_value=store).start()
        patch.object(conversation_service, "OpenAIAnswerProvider", return_value=self.provider).start()
        patch.object(retrieval_service, "retrieve_evidence", return_value=retrieval_result()).start()
        self.addCleanup(patch.stopall)
        self.payload = {"question": "Explain FCFS", "turn_id": str(uuid.uuid4())}

    async def test_valid_request_returns_ordered_sse_and_original_sources(self):
        code, events = await request(self.payload)
        self.assertEqual(code, 200)
        self.assertEqual(events[0]["event"], "session")
        final = events[-1]["data"]
        self.assertEqual(final["status"], "answered")
        self.assertEqual(final["sources"][0]["origins"], retrieval_result()["results"][0]["sources"])

    async def test_invalid_request_is_rejected_before_generation(self):
        for data, course in (({**self.payload, "question": " "}, "42"), ({**self.payload, "turn_id": "bad"}, "42"), (self.payload, "0")):
            self.assertEqual((await request(data, course))[0], 422)
        self.assertEqual(self.provider.calls, [])

    async def test_course_switch_rejects_reusing_old_session(self):
        _, events = await request(self.payload)
        data = {**self.payload, "conversation_id": events[0]["data"]["conversation_id"], "turn_id": str(uuid.uuid4())}
        self.assertEqual((await request(data, "43"))[0], 409)

    async def test_unknown_session_is_safe_conflict(self):
        code, body = await request({**self.payload, "conversation_id": str(uuid.uuid4())})
        self.assertEqual(code, 409)
        self.assertNotIn("student", json.dumps(body))

    async def test_provider_failure_is_stream_error_without_private_details(self):
        self.provider.fail = True
        code, events = await request(self.payload)
        self.assertEqual(code, 200)
        self.assertEqual(events[-1]["event"], "error")
        self.assertNotIn("private", json.dumps(events))
        self.assertFalse(next(iter(self.sessions.sessions.values())).busy)

    async def test_retry_with_same_turn_id_uses_cached_final(self):
        _, first = await request(self.payload)
        _, second = await request({**self.payload, "conversation_id": first[0]["data"]["conversation_id"]})
        self.assertEqual(first[-1], second[-1])
        self.assertEqual(len(self.provider.calls), 1)

    async def test_invalid_citations_are_an_error_event_never_final_sources(self):
        self.provider.bad_citation = True
        code, events = await request(self.payload)
        self.assertEqual(code, 200)
        self.assertEqual(events[-1]["event"], "error")
        self.assertFalse(any(event["event"] == "final" for event in events))
        self.assertEqual(next(iter(self.sessions.sessions.values())).history, [])
