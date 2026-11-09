"""Deterministic conversation, retrieval and streamed-provider regression tests."""
import asyncio
import copy
import json
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from backend.app.conversation_service import ConversationService, ConversationError
from backend.app import rag_service, retrieval_service
from backend.app.llm_service import LLMProviderError, OpenAIAnswerProvider
from backend.app.ingestion_store import IngestionStore
from backend.app.chunking_service import ensure_course_chunks
from backend.app.embedding_service import embed_course
from backend.tests.test_embeddings import FakeProvider
from backend.tests.test_rag import retrieval_result
from backend.app.query_understanding_service import empty_understanding


class FakeConversationProvider:
    def __init__(self):
        self.calls, self.rewrites = [], []
        self.status, self.fail, self.bad_citation = "answered", False, False
        self.closed = False
        self.rewrite = empty_understanding("Static Analysis Homework required warnings", use_active_documents=True)

    async def rewrite_query(self, cid, question, context):
        self.rewrites.append((cid, question, copy.deepcopy(context)))
        return self.rewrite

    async def stream_generate(self, instructions, cid, question, passages, context):
        try:
            self.calls.append((cid, question, passages, copy.deepcopy(context)))
            yield {"event": "answer_delta", "data": {"blocks": ["First"]}}
            await asyncio.sleep(0)
            if self.fail:
                raise LLMProviderError("private upstream failure")
            yield {"event": "answer_delta", "data": {"blocks": ["First complete explanation."]}}
            yield {"event": "complete", "data": {"answer": {"status": self.status,
                "blocks": [{"text": "First complete explanation.", "source_ids": ["S999" if self.bad_citation else "S1"]}] if self.status == "answered" else [],
                "limitations": []}, "model": "fake", "usage": None}}
        finally:
            self.closed = True


class ConversationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = IngestionStore("student", Path(self.temp.name) / "test.sqlite3")
        self.store.save_course({"course_id": 42, "synced_at": "now", "course": {"metadata": {"course_code": "CSE 1000"}}})
        self.store.save_document(42, {"document_key": "file:1", "canvas_id": 1, "course_id": 42,
                                 "source_type": "file", "title": "Static Analysis Homework", "status": "ok",
                                 "version": "1", "text": "Four warnings are required."}, "now")
        self.sessions = ConversationService()
        self.provider = FakeConversationProvider()
        self.retrieval = retrieval_result()
        self.retrieval["results"][0]["sources"][0]["document_key"] = "file:1"
        self.search = patch.object(retrieval_service, "retrieve_evidence", return_value=self.retrieval).start()
        self.addCleanup(patch.stopall)

    async def ask(self, question="Explain Static Analysis Homework", cid=None, turn="turn-1"):
        session = self.sessions.acquire("student", 42, cid)
        events = [event async for event in self.sessions.events(session, question, turn, store=self.store, provider=self.provider)]
        return session, events

    async def test_standalone_stream_contains_real_deltas_then_validated_sources(self):
        session, events = await self.ask()
        self.assertEqual([e["event"] for e in events], ["session", "searching", "generating", "answer_delta", "answer_delta", "final"])
        self.assertEqual(len(self.provider.rewrites), 1)
        self.assertNotIn("sources", events[3]["data"])
        self.assertEqual(events[-1]["data"]["sources"][0]["id"], "S1")
        self.assertEqual(len(session.history), 1)
        self.assertFalse(session.busy)

    async def test_follow_up_rewrites_and_performs_fresh_retrieval(self):
        session, _ = await self.ask()
        await self.ask("How many warnings do I need?", session.id, "turn-2")
        self.assertEqual(self.search.call_count, 2)
        self.assertEqual(self.search.call_args.args[1], self.provider.rewrite["query"])
        self.assertEqual(self.search.call_args.kwargs["focus_documents"], ["file:1"])
        self.assertEqual(self.provider.rewrites[1][2]["active_documents"][0]["title"], "Static Analysis Homework")

    async def test_topic_change_drops_old_document_focus(self):
        session, _ = await self.ask()
        self.provider.rewrite = empty_understanding("Attendance policy", use_active_documents=False)
        await self.ask("What is the attendance policy?", session.id, "turn-2")
        self.assertEqual(self.search.call_args.kwargs.get("focus_documents", []), [])
        self.assertEqual(self.provider.calls[-1][3]["active_documents"], [])

    async def test_named_document_context_survives_an_insufficient_answer(self):
        self.retrieval["resolved_documents"] = ["file:1"]
        self.provider.status = "insufficient_context"
        session, events = await self.ask("What is question 1 in Static Analysis Homework?")
        self.assertEqual(events[-1]["data"]["status"], "insufficient_context")
        self.assertEqual(session.active_documents, ["file:1"])
        self.provider.status = "answered"
        await self.ask("What about question 2?", session.id, "turn-2")
        self.assertEqual(self.search.call_args.kwargs["focus_documents"], ["file:1"])

    async def test_ambiguous_followup_asks_clarification_without_generation(self):
        session, _ = await self.ask()
        self.provider.rewrite["clarification"] = "Which assignment do you mean?"
        _, events = await self.ask("What about the other one?", session.id, "turn-2")
        self.assertEqual(events[-1]["data"]["status"], "clarification_needed")
        self.assertEqual(len(self.provider.calls), 1)

    async def test_duplicate_turn_reuses_completed_result_without_paid_work(self):
        session, events = await self.ask()
        _, repeated = await self.ask(cid=session.id)
        self.assertEqual(events[-1], repeated[-1])
        self.assertEqual(len(self.provider.calls), 1)
        self.assertEqual(len(session.history), 1)

    async def test_turn_id_cannot_be_reused_for_another_question(self):
        session, _ = await self.ask()
        _, events = await self.ask("Different question", session.id)
        self.assertEqual(events[-1]["event"], "error")

    async def test_concurrent_requests_are_rejected(self):
        session = self.sessions.acquire("student", 42)
        with self.assertRaises(ConversationError): self.sessions.acquire("student", 42, session.id)

    async def test_account_course_and_unknown_session_isolation(self):
        session, _ = await self.ask()
        for account, course, sid in (("other", 42, session.id), ("student", 43, session.id), ("student", 42, "missing")):
            with self.assertRaises(ConversationError): self.sessions.acquire(account, course, sid)

    async def test_expired_sessions_and_bounded_session_count(self):
        session, _ = await self.ask()
        session.updated_at = time.monotonic() - 2000
        with self.assertRaises(ConversationError): self.sessions.acquire("student", 42, session.id)
        bounded = ConversationService(limit=1)
        old = bounded.acquire("student", 42); old.busy = False
        new = bounded.acquire("student", 42)
        self.assertNotIn(old.id, bounded.sessions)
        self.assertIn(new.id, bounded.sessions)

    async def test_invalid_citations_discard_draft_and_do_not_commit(self):
        self.provider.bad_citation = True
        session, events = await self.ask()
        self.assertEqual(events[-1]["event"], "error")
        self.assertNotIn("private", json.dumps(events[-1]))
        self.assertEqual(session.history, [])
        self.assertEqual(session.turns, {})

    async def test_provider_failure_is_safe_and_retryable(self):
        self.provider.fail = True
        session, events = await self.ask()
        self.assertEqual(events[-1]["event"], "error")
        self.assertNotIn("private", json.dumps(events[-1]))
        self.provider.fail = False
        _, retried = await self.ask(cid=session.id)
        self.assertEqual(retried[-1]["event"], "final")

    async def test_cancelling_stream_releases_session_without_committing(self):
        session = self.sessions.acquire("student", 42)
        stream = self.sessions.events(session, "Question", "id", store=self.store, provider=self.provider)
        while (await anext(stream))["event"] != "answer_delta": pass
        await stream.aclose()
        self.assertTrue(self.provider.closed)
        self.assertFalse(session.busy)
        self.assertEqual(session.history, [])

    async def test_empty_index_and_insufficient_context_do_not_generate(self):
        for status in ("not_indexed", "no_content"):
            self.search.return_value = {"status": status, "results": []}
            _, events = await self.ask(turn=status)
            self.assertEqual(events[-1]["data"]["status"], "indexing_needed" if status == "not_indexed" else "insufficient_context")
        self.assertEqual(self.provider.calls, [])

    async def test_structured_data_needed_keeps_fixed_deferral(self):
        self.provider.status = "needs_structured_data"
        _, events = await self.ask("When is it due?")
        self.assertEqual(events[-1]["data"]["status"], "needs_structured_data")
        self.assertEqual(events[-1]["data"]["sources"], [])

    async def test_removed_documents_leave_no_stale_active_focus(self):
        session, _ = await self.ask()
        self.store.save_course({"course_id": 42, "synced_at": "later"})
        await self.ask("How many?", session.id, "turn-2")
        self.assertEqual(self.provider.rewrites[1][2]["active_documents"], [])

    async def test_history_context_and_retry_cache_are_bounded(self):
        session = None
        for i in range(15):
            session, _ = await self.ask("Question " + str(i), session.id if session else None, str(i))
        self.assertEqual(len(session.history), 3)
        self.assertEqual(len(session.turns), 12)
        context = self.sessions.context(session, self.store)
        self.assertLessEqual(sum(len(t[r]) for t in context["recent_turns"] for r in ("question", "answer")), 6000)


class EvidenceTests(unittest.TestCase):
    def item(self, number, text, key="file:1", eligible=True):
        return {"text_hash": str(number), "text": text, "sources": [{"document_key": key, "title": "Static Analysis Homework", "course_id": 42,
                 "chunk_index": number, "canvas_id": 1, "source_type": "file", "embedding_eligible": eligible,
                 "source_quality": {"policy_retrieval_eligible": eligible}}]}

    def test_neighbor_expansion_and_duplicate_suppression_are_bounded(self):
        from backend.app.embedding_service import EmbeddingConfiguration
        anchor = self.item(1, "Choose four warnings from at least two categories.")
        neighbor = self.item(2, "Submit screenshots of analyzer output and the required explanations.")
        duplicate = {**anchor, "text_hash": "duplicate"}
        store = Mock()
        store.load_documents.return_value = []
        store.load_embeddings.return_value = {"1": [1], "2": [1], "duplicate": [1]}
        store.load_embedding_inputs.return_value = [anchor, neighbor, duplicate]
        with patch.object(retrieval_service, "search_course", return_value={"status": "ready", "results": [{**anchor, "score": .8}, {**duplicate, "score": .7}]}):
            result = retrieval_service.retrieve_evidence(42, "Explain Static Analysis Homework", store=store)
        self.assertEqual(len(result["results"]), 2)
        self.assertTrue(any(item["selection"] in ("neighbor", "same_document") for item in result["results"] if "selection" in item))

    def test_assignment_overview_includes_requirements_and_their_neighbor(self):
        overview = self.item(0, "This assignment uses an analysis tool on your project.")
        overview["sources"][0]["references"] = [{"source_type": "assignment", "canvas_id": 5}]
        setup = self.item(1, "Install a suitable tool and document the setup procedure.")
        requirements = self.item(2, "For each selected result, answer the required analysis questions.")
        ending = self.item(3, "Give a concrete input scenario and justify each conclusion.")
        lecture = self.item(0, "Related industry background about program analysis.", "file:2")
        lecture["text_hash"] = "lecture"
        all_items = [overview, setup, requirements, ending, lecture]
        store = Mock()
        store.load_documents.return_value = []
        store.load_embeddings.return_value = {item["text_hash"]: [1] for item in all_items}
        store.load_embedding_inputs.return_value = all_items
        with patch.object(retrieval_service, "search_course", return_value={"status": "ready", "results": [{**lecture, "score": .9}, {**overview, "score": .85}, {**setup, "score": .8}]}):
            result = retrieval_service.retrieve_evidence(42, "Explain Static Analysis Homework", store=store)
        texts = [item["text"] for item in result["results"]]
        self.assertIn(requirements["text"], texts)
        self.assertIn(ending["text"], texts)
        self.assertLessEqual(sum(map(len, texts)), 16000)

    def test_unindexed_and_policy_ineligible_neighbors_are_excluded(self):
        anchor = self.item(1, "Course concept explanation.")
        blocked = self.item(2, "Secret template content.", eligible=False)
        missing = self.item(3, "Not yet indexed.")
        store = Mock()
        store.load_documents.return_value = []
        store.load_embeddings.return_value = {"1": [1], "2": [1]}
        store.load_embedding_inputs.return_value = [anchor, blocked, missing]
        with patch.object(retrieval_service, "search_course", return_value={"status": "partial", "results": [{**anchor, "score": .8}]}):
            result = retrieval_service.retrieve_evidence(42, "Course concept", store=store)
        self.assertEqual(len(result["results"]), 1)


class ProviderStreamTests(unittest.IsolatedAsyncioTestCase):
    def client(self, events):
        class Stream:
            async def __aenter__(self): return self
            async def __aexit__(self, *args): pass
            async def __aiter__(self):
                for event in events: yield SimpleNamespace(**event)
        client = Mock()
        client.responses.stream.return_value = Stream()
        factory = patch("openai.AsyncOpenAI")
        self.addCleanup(patch.stopall)
        mocked = factory.start()
        mocked.return_value.__aenter__.return_value = client
        return client

    async def test_sdk_stream_emits_before_complete_and_keeps_inputs_separate(self):
        payload = json.dumps({"status": "answered", "blocks": [{"text": "Four warnings", "source_ids": ["S1"]}], "limitations": []})
        split = payload.index("warnings")
        client = self.client([{"type": "response.output_text.delta", "delta": payload[:split]},
                              {"type": "response.output_text.delta", "delta": payload[split:]},
                              {"type": "response.completed", "response": SimpleNamespace(status="completed", usage=None)}])
        provider = OpenAIAnswerProvider(api_key="fake-key", usage_recorder=False)
        events = [e async for e in provider.stream_generate("rules", 42, "Question", [{"id": "S1"}], {"recent_turns": []})]
        self.assertEqual(events[0]["event"], "answer_delta")
        self.assertEqual(events[-1]["event"], "complete")
        kwargs = client.responses.stream.call_args.kwargs
        self.assertFalse(kwargs["store"])
        data = json.loads(kwargs["input"])
        self.assertIn("conversation_context", data)
        self.assertIn("passages", data)
        self.assertNotIn("fake-key", kwargs["input"])

    async def test_missing_completion_and_provider_error_are_rejected(self):
        for events in ([], [{"type": "error"}]):
            self.client(events)
            with self.assertRaises(LLMProviderError):
                async for _ in OpenAIAnswerProvider(api_key="fake-key", usage_recorder=False).stream_generate("rules",42,"Question",[{"id":"S1"}],{}): pass
