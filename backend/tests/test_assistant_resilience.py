"""Question understanding survives transient provider trouble; unsafe guesses are never made; failures leave a trail."""
import asyncio
import copy
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx2 as httpx
import openai

from backend.app import assistant_events, llm_service, main, query_understanding_service as qus, rag_service
from backend.app.api_usage_service import UsageRecorder
from backend.app.chunking_service import ensure_course_chunks
from backend.app.conversation_service import ConversationService
from backend.app.ingestion_store import IngestionStore
from backend.app.llm_service import LLMProviderError, OpenAIAnswerProvider
from backend.app.query_understanding_service import DegradedPlan, FALLBACK_NOTICE, empty_understanding, fallback_understanding
from backend.tests.test_rag import retrieval_result
from backend.tests.test_retention_api import request


def api_error(cls, status, *, headers=None, code=None, body=None):
    response = httpx.Response(status, request=httpx.Request("POST", "https://api.openai.com/v1/responses"), headers=headers or {})
    error = cls("provider text that includes the question: what is my secret grade", response=response, body=body)
    if code:
        error.code = code
    return error


def connection_error():
    return openai.APIConnectionError(request=httpx.Request("POST", "https://api.openai.com/v1/responses"))


RATE = lambda **kw: api_error(openai.RateLimitError, 429, **kw)


async def get_with_query(path, query=""):
    """Like the shared test helper, but passes a query string (the shared one drops it)."""
    messages = []
    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}
    async def send(message):
        messages.append(message)
    await main.app({"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "GET", "scheme": "http", "path": path,
                    "raw_path": path.encode(), "query_string": query.encode(), "root_path": "", "headers": [],
                    "server": ("test", 80), "client": ("test", 1)}, receive, send)
    return (next(m["status"] for m in messages if m["type"] == "http.response.start"),
            json.loads(b"".join(m.get("body", b"") for m in messages if m["type"] == "http.response.body")))


class TransientClassificationTests(unittest.TestCase):
    def test_what_is_worth_retrying(self):
        transient = [RATE(), api_error(openai.InternalServerError, 500), api_error(openai.InternalServerError, 503), connection_error(),
                     openai.APITimeoutError(request=httpx.Request("POST", "https://x")), api_error(openai.APIStatusError, 408),
                     api_error(openai.APIStatusError, 409)]
        permanent = [api_error(openai.AuthenticationError, 401), api_error(openai.PermissionDeniedError, 403),
                     api_error(openai.BadRequestError, 400), api_error(openai.NotFoundError, 404),
                     RATE(code="insufficient_quota"), ValueError("x"), RuntimeError("y")]
        self.assertTrue(all(llm_service.is_transient(e) for e in transient))
        self.assertFalse(any(llm_service.is_transient(e) for e in permanent))

    def test_retry_after_is_read_and_capped(self):
        self.assertEqual(llm_service._retry_after(RATE(headers={"retry-after": "3"})), 3.0)
        self.assertEqual(llm_service._retry_after(RATE(headers={"retry-after": "120"})), llm_service.MAX_RETRY_AFTER)
        for header in ({}, {"retry-after": "soon"}, {"retry-after": "-5"}):
            value = llm_service._retry_after(RATE(headers=header))
            self.assertIn(value, (None, 0.0))


class Base(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.store = IngestionStore("resilience", Path(self.directory.name) / "t.sqlite3")
        self.store.save_course({"course_id": 42, "synced_at": "now", "course": {"metadata": {"course_code": "CSE 1000"}}})
        self.sleeps = []
        async def fake_sleep(seconds): self.sleeps.append(round(seconds, 3))
        patch.object(llm_service, "_sleep", fake_sleep).start()
        patch.object(llm_service, "_jitter", lambda: 0.0).start()
        self.addCleanup(patch.stopall)

    def provider(self):
        return OpenAIAnswerProvider(api_key="fake-key", usage_recorder=UsageRecorder(self.store))

    def events(self):
        return [(e["outcome"], e["error_class"], e["attempt"]) for e in self.store.load_assistant_events()]


class RetryLoopTests(Base):
    async def test_success_on_the_first_try_records_nothing(self):
        call = AsyncMock(return_value="ok")
        self.assertEqual(await self.provider()._with_retries(call, stage="query_understanding", model="m"), "ok")
        self.assertEqual((call.await_count, self.sleeps, self.events()), (1, [], []))

    async def test_two_transient_failures_then_success_backs_off_and_records_recovery(self):
        call = AsyncMock(side_effect=[RATE(), connection_error(), "ok"])
        self.assertEqual(await self.provider()._with_retries(call, stage="query_understanding", model="m"), "ok")
        self.assertEqual((call.await_count, self.sleeps), (3, [1.0, 2.5]))
        self.assertEqual(self.events(), [("retry", "RateLimitError", 1), ("retry", "APIConnectionError", 2), ("recovered", None, 3)])

    async def test_it_gives_up_after_the_last_retry_and_raises_the_original_error(self):
        error = RATE()
        call = AsyncMock(side_effect=[error, error, error, error])
        with self.assertRaises(openai.RateLimitError):
            await self.provider()._with_retries(call, stage="query_understanding", model="m")
        self.assertEqual((call.await_count, self.sleeps), (3, [1.0, 2.5]))                # 3 attempts in total
        self.assertEqual(self.events(), [("retry", "RateLimitError", 1), ("retry", "RateLimitError", 2), ("failed", "RateLimitError", 3)])

    async def test_permanent_errors_are_not_retried(self):
        for error in (api_error(openai.AuthenticationError, 401), api_error(openai.BadRequestError, 400), RATE(code="insufficient_quota")):
            self.store = IngestionStore("fresh" + type(error).__name__, Path(self.directory.name) / "t.sqlite3")
            call = AsyncMock(side_effect=[error, "never"])
            with self.assertRaises(openai.OpenAIError):
                await self.provider()._with_retries(call, stage="query_understanding", model="m")
            self.assertEqual(call.await_count, 1)
            self.assertEqual([e[0] for e in self.events()], ["failed"])
        self.assertEqual(self.sleeps, [])

    async def test_retry_after_from_the_provider_is_honored(self):
        call = AsyncMock(side_effect=[RATE(headers={"retry-after": "4"}), RATE(headers={"retry-after": "99"}), "ok"])
        await self.provider()._with_retries(call, stage="query_understanding", model="m")
        self.assertEqual(self.sleeps, [4.0, llm_service.MAX_RETRY_AFTER])

    async def test_events_never_contain_the_question_or_provider_text(self):
        call = AsyncMock(side_effect=[RATE()] * 4)
        with self.assertRaises(openai.RateLimitError):
            await self.provider()._with_retries(call, stage="query_understanding", model="m")
        stored = json.dumps(self.store.load_assistant_events())
        self.assertNotIn("secret grade", stored)
        self.assertNotIn("provider text", stored)
        self.assertNotIn("fake-key", stored)

    async def test_a_provider_without_a_store_still_retries_and_records_nothing(self):
        provider = OpenAIAnswerProvider(api_key="fake-key", usage_recorder=False)
        call = AsyncMock(side_effect=[RATE(), "ok"])
        self.assertEqual(await provider._with_retries(call, stage="query_understanding", model="m"), "ok")
        self.assertEqual(self.sleeps, [1.0])

    async def test_recording_problems_never_break_the_question(self):
        broken = SimpleNamespace(record_assistant_event=lambda entry: (_ for _ in ()).throw(OSError("disk full")))
        provider = OpenAIAnswerProvider(api_key="fake-key", usage_recorder=SimpleNamespace(store=broken))
        self.assertEqual(await provider._with_retries(AsyncMock(side_effect=[RATE(), "ok"]), stage="query_understanding", model="m"), "ok")


class RewriteQueryTests(Base):
    async def test_rewrite_query_retries_once_the_rate_limit_clears_and_uses_our_own_retry_settings(self):
        plan = empty_understanding("Explain processes")
        good = SimpleNamespace(id="r1", status="completed", output_text=json.dumps(plan), usage=SimpleNamespace(input_tokens=5, output_tokens=5))
        with patch("openai.AsyncOpenAI") as factory:
            client = factory.return_value.__aenter__.return_value
            client.responses.create = AsyncMock(side_effect=[RATE(), good])
            result = await self.provider().rewrite_query(42, "Explain processes", {})
        self.assertEqual(result, plan)
        self.assertEqual(client.responses.create.await_count, 2)
        constructor = factory.call_args.kwargs
        self.assertEqual((constructor["max_retries"], constructor["timeout"]), (0, llm_service.UNDERSTANDING_TIMEOUT))   # no hidden extra retries
        self.assertEqual([e[0] for e in self.events()], ["retry", "recovered"])

    async def test_a_persistent_outage_becomes_a_safe_provider_error(self):
        with patch("openai.AsyncOpenAI") as factory:
            factory.return_value.__aenter__.return_value.responses.create = AsyncMock(side_effect=RATE())
            with self.assertRaises(LLMProviderError) as caught:
                await self.provider().rewrite_query(42, "Explain processes", {})
        self.assertNotIn("secret grade", str(caught.exception))
        self.assertEqual(self.sleeps, [1.0, 2.5])


class FallbackSafetyTests(unittest.TestCase):
    def test_safe_questions_get_a_plain_course_search_plan(self):
        for question in ("What is the late policy for this course?", "Explain how scheduling algorithms work in the lectures",
                         "which topics does the syllabus say the course covers", "Tell me about the grading breakdown please"):
            plan = fallback_understanding(question)
            self.assertIsInstance(plan, DegradedPlan, question)
            self.assertEqual((plan["intent"], plan["query"], plan["use_active_documents"], plan["operation"]), ("course_wide_question", question, False, "topic_switch"))
            qus.validate_understanding(plan)                                                   # a complete, valid contract

    def test_questions_that_need_live_canvas_facts_never_fall_back(self):
        for question in ("When is the next assignment due?", "what is due tomorrow in this class", "did I submit homework one yet",
                         "what is my grade in this course", "how many points is the final project worth", "what's the deadline for the paper"):
            self.assertIsNone(fallback_understanding(question), question)

    def test_specific_numbered_work_never_falls_back_so_one_cannot_become_ten(self):
        for question in ("Explain homework 1 to me in detail", "what does lab 3 ask me to do", "summarize chapter 5 of the book please",
                         "help me understand HW2 question four", "what is on quiz #2", "tell me about Iteration 3 deliverables", "explain the lab3 instructions"):
            self.assertIsNone(fallback_understanding(question), question)

    def test_solution_requests_never_fall_back(self):
        for question in ("solve this problem for me and give the full answer", "write the code for the merge sort assignment for me",
                         "just give me the solution to the project"):
            self.assertIsNone(fallback_understanding(question), question)

    def test_follow_ups_and_tiny_questions_never_fall_back(self):
        for question in ("and the written one?", "what about the other one", "why is that", "explain it again please", "same for the lab",
                         "ok thanks", "", "   ", None, "what"):
            self.assertIsNone(fallback_understanding(question), repr(question))


class UnderstandQuestionTests(Base):
    class Failing:
        def __init__(self, error=None, value=None): self.error, self.value, self.calls = error, value, 0
        async def rewrite_query(self, cid, question, context):
            self.calls += 1
            if self.error: raise self.error
            return self.value

    async def understand(self, provider, question):
        return await qus.understand_question(42, question, store=self.store, provider=provider)

    async def test_a_healthy_provider_is_unchanged_and_leaves_no_trail(self):
        plan = await self.understand(self.Failing(value=empty_understanding("Explain processes please now")), "Explain processes please now")
        self.assertNotIsInstance(plan, DegradedPlan)
        self.assertEqual(self.events(), [])

    async def test_provider_failure_falls_back_for_a_safe_question_and_records_it(self):
        plan = await self.understand(self.Failing(error=LLMProviderError("down")), "What is the late policy for this course?")
        self.assertIsInstance(plan, DegradedPlan)
        self.assertEqual([e[0] for e in self.events()], ["fallback"])
        self.assertEqual(self.events()[0][1], "LLMProviderError")

    async def test_provider_failure_still_raises_for_an_unsafe_question_and_records_it(self):
        for question in ("When is homework 3 due?", "and the written one?", "solve problem four for me please"):
            with self.assertRaises(LLMProviderError, msg=question):
                await self.understand(self.Failing(error=LLMProviderError("down")), question)
        self.assertEqual([e[0] for e in self.events()], ["failed"] * 3)

    async def test_a_rejected_plan_also_falls_back_when_safe(self):
        bad = {"intent": "invented", "query": "x"}
        plan = await self.understand(self.Failing(value=bad), "What topics does the syllabus say are covered?")
        self.assertIsInstance(plan, DegradedPlan)
        self.assertEqual([e[0] for e in self.events()], ["fallback"])
        with self.assertRaises(qus.PlanRejected):
            await self.understand(self.Failing(value=bad), "When is homework 3 due?")


class DiagnosticsTests(Base):
    async def test_summary_counts_by_stage_outcome_and_class(self):
        provider = self.provider()
        with self.assertRaises(openai.RateLimitError):
            await provider._with_retries(AsyncMock(side_effect=[RATE()] * 4), stage="query_understanding", model="m")
        await provider._with_retries(AsyncMock(side_effect=[connection_error(), "ok"]), stage="query_understanding", model="m")
        summary = assistant_events.summarize(self.store, hours=24)
        counts = {(r["stage"], r["outcome"], r["error_class"]): r["count"] for r in summary["events"]}
        self.assertEqual(counts[("query_understanding", "retry", "RateLimitError")], 2)
        self.assertEqual(counts[("query_understanding", "failed", "RateLimitError")], 1)
        self.assertEqual(counts[("query_understanding", "recovered", None)], 1)
        self.assertEqual(counts[("query_understanding", "retry", "APIConnectionError")], 1)
        self.assertEqual(summary["total_events"], 5)       # 3 events (retry, retry, failed) + 2 (retry, recovered)

    async def test_old_events_age_out_of_the_window_and_the_log_stays_bounded(self):
        store = self.store
        store.record_assistant_event({"recorded_at": "2020-01-01T00:00:00+00:00", "stage": "s", "outcome": "failed"})
        assistant_events.record(store, "s", "retry", error=RATE(), attempt=1)
        self.assertEqual(assistant_events.summarize(store, hours=24)["total_events"], 1)
        with patch.object(IngestionStore, "ASSISTANT_EVENT_LIMIT", 5):
            for _ in range(12):
                assistant_events.record(store, "s", "retry", attempt=1)
        self.assertEqual(len(store.load_assistant_events()), 5)

    def test_the_diagnostics_endpoint_returns_counts_and_validates_hours(self):
        assistant_events.record(self.store, "query_understanding", "fallback", error=LLMProviderError("x"))
        with patch.object(main, "default_store", return_value=self.store):
            code, data = asyncio.run(get_with_query("/api/assistant/diagnostics", "hours=24"))
            self.assertEqual((code, data["total_events"]), (200, 1))
            self.assertEqual(data["recent"][0]["outcome"], "fallback")
            self.assertEqual(set(data["recent"][0]), {"recorded_at", "stage", "outcome", "error_class", "status_code", "attempt"})
            for bad in ("0", "721", "abc"):
                self.assertEqual(asyncio.run(get_with_query("/api/assistant/diagnostics", f"hours={bad}"))[0], 422)
            self.assertEqual(asyncio.run(get_with_query("/api/assistant/diagnostics"))[1]["hours"], 24)      # default window


class FallbackAnswerTests(Base):
    """End to end: the student still gets a cited answer, and is told how it was produced."""
    class Provider:
        def __init__(self, error): self.error = error; self.calls = []
        async def rewrite_query(self, cid, question, context): raise self.error
        def generate(self, *args):
            self.calls.append(args)
            return {"answer": {"status": "answered", "blocks": [{"text": "The late policy is in the syllabus.", "source_ids": ["S1"]}], "limitations": []},
                    "model": "fake", "usage": None}
        async def stream_generate(self, instructions, cid, question, passages, context):
            self.calls.append((cid, question))
            yield {"event": "answer_delta", "data": {"blocks": ["The late policy is in the syllabus."]}}
            yield {"event": "complete", "data": {"answer": {"status": "answered", "limitations": [],
                    "blocks": [{"text": "The late policy is in the syllabus.", "source_ids": ["S1"]}]}, "model": "fake", "usage": None}}

    def setUp(self):
        super().setUp()
        self.store.save_document(42, {"document_key": "file:1", "canvas_id": 1, "course_id": 42, "source_type": "file", "title": "Syllabus",
                                      "status": "ok", "version": "1", "text": "Late work loses ten percent."}, "now")
        retrieval = retrieval_result()
        retrieval["results"][0]["sources"][0]["document_key"] = "file:1"
        patch("backend.app.rag_service.retrieval_router.route_query", side_effect=lambda cid, q, plan, **k: {
            **copy.deepcopy(retrieval), "query_understanding": plan, "retrieval_route": "course_material"}).start()
        patch("backend.app.rag_service.default_store", return_value=self.store).start()

    def test_non_streaming_answer_succeeds_with_the_notice(self):
        provider = self.Provider(LLMProviderError("down"))
        result = rag_service.answer_question(42, "What is the late policy for this course?", store=self.store, llm_provider=provider)
        self.assertEqual(result["status"], "answered")
        self.assertIn(FALLBACK_NOTICE, result["warnings"])
        self.assertEqual(len(provider.calls), 1)

    def test_non_streaming_unsafe_question_still_errors(self):
        with self.assertRaises(LLMProviderError):
            rag_service.answer_question(42, "When is the next assignment due?", store=self.store, llm_provider=self.Provider(LLMProviderError("down")))

    def test_a_normal_answer_has_no_fallback_notice(self):
        class Healthy(self.Provider):
            async def rewrite_query(self, cid, question, context): return empty_understanding(question)
        result = rag_service.answer_question(42, "What is the late policy for this course?", store=self.store, llm_provider=Healthy(None))
        self.assertNotIn(FALLBACK_NOTICE, result["warnings"])

    async def test_streaming_conversation_answers_with_the_notice(self):
        provider = self.Provider(LLMProviderError("down"))
        sessions = ConversationService()
        session = sessions.acquire("resilience", 42)
        events = [e async for e in sessions.events(session, "What is the late policy for this course?", "t1", store=self.store, provider=provider)]
        final = events[-1]
        self.assertEqual(final["event"], "final")
        self.assertEqual(final["data"]["status"], "answered")
        self.assertIn(FALLBACK_NOTICE, final["data"]["warnings"])

    async def test_streaming_conversation_unsafe_question_reports_a_retryable_error(self):
        sessions = ConversationService()
        session = sessions.acquire("resilience", 42)
        events = [e async for e in sessions.events(session, "when is homework 2 due?", "t2", store=self.store, provider=self.Provider(LLMProviderError("down")))]
        self.assertEqual(events[-1]["event"], "error")
        self.assertIn("Please retry", events[-1]["data"]["message"])


if __name__ == "__main__":
    unittest.main()
