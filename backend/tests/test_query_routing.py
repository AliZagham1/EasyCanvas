"""Fake-provider intent/routing regressions; printed traces are not live model evals."""

import asyncio
import copy
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import requests

from backend.app import canvas_service, rag_service
from backend.app.chunking_service import ensure_course_chunks
from backend.app.conversation_service import ConversationService
from backend.app.document_reference_service import resolve_assignment_reference
from backend.app.embedding_service import embed_course
from backend.app.ingestion_store import IngestionStore
from backend.app.llm_service import OpenAIAnswerProvider, LLMProviderError
from backend.app.query_understanding_service import empty_understanding, validate_understanding, query_schema, QUERY_INSTRUCTIONS
from backend.app.retrieval_router import route_query, _readable_document_keys
from backend.app.structured_canvas_service import date_window, due_in_window, rubric_evidence
from backend.tests.test_embeddings import FakeProvider


NOW = datetime(2026, 11, 1, 18, tzinfo=timezone.utc)


def assignment(aid, title, description, due="2026-11-06T05:59:00Z", rubric=None):
    raw = {"id": aid, "course_id": 42, "name": title, "description": description, "due_at": due,
           "points_possible": 100, "submission": {"workflow_state": "unsubmitted", "submitted_at": None},
           "html_url": f"https://canvas.example/courses/42/assignments/{aid}"}
    if rubric:
        raw.update(rubric=rubric, rubric_settings={"title": "Presentation Rubric", "points_possible": 100})
    return {"canvas_id": aid, "course_id": 42, "source_type": "assignment", "title": title,
            "url": raw["html_url"], "metadata": raw}


class PlannedProvider:
    def __init__(self, plan):
        self.plan, self.understanding_calls, self.answer_calls = plan, [], []

    async def rewrite_query(self, cid, question, context):
        self.understanding_calls.append((cid, question, copy.deepcopy(context)))
        return copy.deepcopy(self.plan)

    def generate(self, instructions, cid, question, passages):
        self.answer_calls.append((instructions, cid, question, copy.deepcopy(passages)))
        return {"answer": {"status": "answered", "blocks": [{"text": "A supported explanation.",
                 "source_ids": [p["id"] for p in passages]}], "limitations": []}, "model": "fake", "usage": None}

    async def stream_generate(self, instructions, cid, question, passages, context):
        yield {"event": "answer_delta", "data": {"blocks": ["A supported"]}}
        yield {"event": "complete", "data": self.generate(instructions, cid, question, passages)}


class RoutingFixture:
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.store = IngestionStore("student", Path(self.temp.name) / "routing.sqlite3")
        rubric = [{"id": f"c{i}", "description": name, "points": points,
                   "long_description": "Criterion instructions.", "ratings": [
                       {"id": f"r{i}", "description": "Strong", "long_description": "Specific actual expectations.", "points": points},
                       {"id": f"z{i}", "description": "No submission", "long_description": "", "points": 0}]}
                  for i, (name, points) in enumerate((("Content", 24), ("Slides", 24), ("Presentation style", 24), ("Demo", 28)))]
        self.assignments = [assignment(101, "HW 1", "Analyze findLast and countPositive, parts a-e. Part f is not required."),
                            assignment(110, "HW 10", "A different homework."),
                            assignment(106, "HW 6", "Implement the parser and submit a report.", "2026-11-03T05:59:00Z"),
                            assignment(201, "Iteration 3 Presentation", "Presentation order only.", rubric=rubric),
                            assignment(202, "Iteration 3 Review", "Review another team."),
                            assignment(203, "Iteration 3 (Written Deliverable)", "Submit a written report.")]
        self.snapshot = {"course_id": 42, "synced_at": "2026-11-01T12:00:00Z",
                         "course": {"metadata": {"course_code": "CSE 3311", "name": "Software Engineering", "time_zone": "America/Chicago"}},
                         "assignments": self.assignments}
        self.store.save_course(self.snapshot)
        for a in self.assignments:
            self.store.save_document(42, {"document_key": f"assignment:{a['canvas_id']}", "canvas_id": a["canvas_id"],
                "course_id": 42, "title": a["title"], "source_type": "assignment", "status": "ok", "text": a["metadata"]["description"]}, self.snapshot["synced_at"])
        for fid, title, aid, text in ((501, "HW1_questions.pdf", 101, "For findLast and countPositive, explain the fault and analyze test behavior."),
                                      (502, "HW1_solutions.pdf", 101, "Published solution: repair both conditions."),
                                      (510, "HW10_questions.pdf", 110, "Homework ten instructions."),
                                      (506, "HW6_questions.pdf", 106, "Implement a parser, test it, and submit a report."),
                                      (1, "Practice Questions 1.pdf", None, "Question 7: matrix operations."),
                                      (10, "Practice Questions 10.pdf", None, "Question 7: orthogonal projection.")):
            self.store.save_document(42, {"document_key": f"file:{fid}", "canvas_id": fid, "course_id": 42,
                "source_type": "file", "title": title, "status": "ok", "text": text,
                "references": [{"source_type": "assignment", "canvas_id": aid}] if aid else []}, self.snapshot["synced_at"])
        ensure_course_chunks(42, self.store)
        self.embedding = FakeProvider();embed_course(42, store=self.store, provider=self.embedding);self.embedding.calls.clear()

    def plan(self, question, intent, **changes):
        return empty_understanding(question, intent=intent, **changes)

    def route(self, question, plan, context=None):
        result = route_query(42, question, plan, store=self.store, provider=self.embedding,
                             conversation_context=context, now=NOW)
        print(json.dumps({"test_kind": "fake-provider-routing", "question": question, "parsed": result["query_understanding"],
                          "route": result["retrieval_route"], "assignments": result.get("resolved_assignments", []),
                          "sources": sorted({o['document_key'] for r in result['results'] for o in r['sources']})}))
        return result


class QueryContractTests(unittest.TestCase):
    def test_complete_schema_rejects_invented_ids_unknown_intents_and_bad_numbers(self):
        valid = empty_understanding("Explain homework 1")
        for update in ({"assignment_id": 999}, {"intent": "invented"}, {"homework_number": "one"},
                       {"requires_live_canvas": "false"}, {"query": ""}, {"requested_data": ["secret"]}):
            with self.subTest(update=update), self.assertRaises(ValueError):validate_understanding({**valid, **update})
        self.assertFalse(query_schema()["additionalProperties"])

    def test_number_fields_keep_their_exact_values(self):
        value = empty_understanding("Practice Questions 10 question 7", document_reference="Practice Questions 10", question_number="7")
        self.assertEqual(validate_understanding(value), value)

    def test_native_titles_keep_iteration_role_and_assignment_numbers(self):
        records = [assignment(1, "HW 1", ""), assignment(10, "HW 10", ""),
                   assignment(3, "Iteration 3 Presentation", ""), assignment(4, "Iteration 3 Review", "")]
        self.assertEqual(resolve_assignment_reference("homework #1", records)["assignment"]["canvas_id"], 1)
        self.assertEqual(resolve_assignment_reference("Iteration 3 Presentation", records)["assignment"]["canvas_id"], 3)
        self.assertEqual(resolve_assignment_reference("Iteration 3", records)["status"], "ambiguous")

    def test_query_instructions_separate_action_from_modifiers_and_authority(self):
        for phrase in ("ACTION BEFORE MODIFIERS", "do not calculate dates", "Never invent", "how something is graded", "Previous answers are not factual evidence"):
            self.assertIn(phrase.casefold(), QUERY_INSTRUCTIONS.casefold())

    def test_course_local_date_windows_are_timezone_aware_and_half_open(self):
        window = date_window("tomorrow", NOW, "America/Chicago")
        self.assertEqual(window[0].date().isoformat(), "2026-11-02")
        self.assertTrue(due_in_window({"due_at": "2026-11-03T05:59:00Z"}, window))
        self.assertFalse(due_in_window({"due_at": "2026-11-03T06:00:00Z"}, window))
        with self.assertRaises(ValueError):due_in_window({"due_at": "2026-11-02T12:00:00"}, window)


class RouterTests(RoutingFixture, unittest.TestCase):
    def test_explicit_syllabus_alias_uses_readable_canonical_copy(self):
        documents = [{'document_key': 'syllabus_file:link', 'content_usable': False,
                      'canonical_document_key': 'file:original'},
                     {'document_key': 'file:original', 'content_usable': True, 'text': 'Attendance policy text.'}]
        self.assertEqual(_readable_document_keys(['syllabus_file:link'], documents), ['file:original'])

    def test_course_policy_prefers_readable_syllabus_over_same_named_empty_grade_item(self):
        attendance = assignment(301, 'Attendance', None)
        self.snapshot['assignments'].append(attendance)
        self.store.save_course(self.snapshot)
        self.store.save_document(42, {'document_key': 'assignment:301', 'canvas_id': 301,
            'course_id': 42, 'source_type': 'assignment', 'title': 'Attendance',
            'status': 'no_text', 'text': ''}, self.snapshot['synced_at'])
        self.store.save_document(42, {'document_key': 'syllabus_file:example', 'course_id': 42,
            'source_type': 'syllabus_file', 'source_role': 'course_syllabus',
            'title': 'Course Syllabus.docx', 'status': 'ok',
            'text': 'Attendance is not required at every meeting. Below 80 percent attendance reduces the overall score by five points.'},
            self.snapshot['synced_at'])
        ensure_course_chunks(42, self.store)
        embed_course(42, store=self.store, provider=self.embedding)
        self.embedding.calls.clear()
        question = 'What is the attendance policy for this class?'
        for plan in (self.plan(question, 'course_wide_question', assignment_reference='Attendance',
                               document_reference='Course Syllabus.docx'),
                     self.plan(question, 'course_wide_question', assignment_reference='Attendance'),
                     self.plan(question, 'explain_assignment', assignment_reference='Attendance')):
            with self.subTest(intent=plan['intent'], document=plan['document_reference']):
                result = self.route(question, plan)
                self.assertEqual(result['retrieval_route'], 'course_policy_material')
                self.assertEqual(result['resolved_documents'], ['syllabus_file:example'])
                self.assertEqual({source['document_key'] for item in result['results'] for source in item['sources']},
                                 {'syllabus_file:example'})

        for question in ('What are the late work rules for this class?',
                         'How are absences handled?',
                         'What is the academic integrity policy?'):
            with self.subTest(question=question):
                result = self.route(question, self.plan(question, 'course_wide_question'))
                self.assertEqual(result['retrieval_route'], 'course_policy_material')
                self.assertEqual(result['resolved_documents'], ['syllabus_file:example'])

        with patch.object(canvas_service, 'get_assignment', return_value=attendance['metadata']) as live:
            facts = self.route('How many points is Attendance worth?',
                               self.plan('How many points is Attendance worth?', 'get_assignment_details',
                                         assignment_reference='Attendance', requested_data=['points']))
        live.assert_called_once_with(42, 301)
        self.assertEqual(facts['retrieval_route'], 'current_assignment_facts')

    def test_policy_without_syllabus_does_not_lock_to_empty_grade_item(self):
        question = 'What is the attendance policy for this class?'
        result = self.route(question, self.plan(question, 'course_wide_question', assignment_reference='Attendance'))
        self.assertEqual(result['retrieval_route'], 'course_policy_material')
        self.assertNotIn('assignment:301', result.get('resolved_documents', []))

    def test_assignment_alias_regressions(self):
        for q in ("explain HW1", "explain homework 1", "explain homework #1", "what is part b of hw 1"):
            intent = "explain_question" if "part b" in q else "explain_assignment"
            result = self.route(q, self.plan(q, intent, assignment_reference="Homework 1", homework_number="1", part="b" if "part b" in q else ""))
            self.assertEqual(result["resolved_assignments"], [101])
            self.assertEqual({o["document_key"] for r in result["results"] for o in r["sources"]}, {"assignment:101", "file:501"})

    def test_due_modifier_does_not_trigger_deadline_route_or_live_request(self):
        q = "what is being asked in homework 6 that is due"
        with patch.object(canvas_service, "get_assignment") as live, patch.object(canvas_service, "get_assignments") as listing:
            result = self.route(q, self.plan(q, "explain_assignment", assignment_reference="Homework 6", homework_number="6"))
        self.assertEqual(result["retrieval_route"], "assignment_material")
        self.assertIn("Implement", " ".join(r["text"] for r in result["results"]))
        live.assert_not_called();listing.assert_not_called()

    def test_deadline_route_always_uses_current_canvas_and_never_vectors(self):
        q = "when is homework 6 due"
        with patch.object(canvas_service, "get_assignment", return_value=self.assignments[2]["metadata"]) as live:
            result = self.route(q, self.plan(q, "get_deadline", assignment_reference="Homework 6", requires_live_canvas=False))
        live.assert_called_once_with(42, 106)
        self.assertTrue(result["query_understanding"]["requires_live_canvas"])
        self.assertEqual(result["retrieval_route"], "current_assignment_facts")
        self.assertFalse(self.embedding.calls)

    def test_missing_named_assignment_is_not_replaced_by_another_due_tomorrow(self):
        with patch.object(canvas_service, "get_assignments") as live:
            result = self.route("when is Homework 999 due tomorrow", self.plan("Homework 999", "get_deadline",
                                assignment_reference="Homework 999", deadline_reference="tomorrow"))
        live.assert_not_called()
        self.assertFalse(result["results"])

    def test_explain_assignment_identified_by_due_tomorrow(self):
        q = "what do I need to do for the assignment due tomorrow"
        with patch.object(canvas_service, "get_assignments", return_value=[a["metadata"] for a in self.assignments]) as live:
            result = self.route(q, self.plan(q, "explain_assignment", deadline_reference="tomorrow"))
        live.assert_called_once_with(42)
        self.assertEqual(result["resolved_assignments"], [106])
        self.assertEqual(result["retrieval_route"], "assignment_material")

    def test_multiple_assignments_in_date_window_ask_which_one(self):
        q = "explain the assignment due tomorrow"
        records = [self.assignments[2]["metadata"], {**self.assignments[0]["metadata"], "due_at": self.assignments[2]["metadata"]["due_at"]}]
        with patch.object(canvas_service, "get_assignments", return_value=records):
            result = self.route(q, self.plan(q, "explain_assignment", deadline_reference="tomorrow"))
        self.assertEqual(result["notice_status"], "clarification_needed")
        self.assertFalse(self.embedding.calls)

    def test_rubric_aliases_use_the_same_actual_saved_criteria(self):
        context = {"active_assignments": [{"assignment_id": 201}], "active_documents": []}
        for q in ("explain the rubric for Iteration 3 Presentation", "how is Iteration 3 Presentation graded",
                  "what is the grading breakdown for Iteration 3 Presentation", "how many points is each criterion worth"):
            followup = "each criterion" in q
            plan = self.plan(q, "explain_rubric", assignment_reference="" if followup else "Iteration 3 Presentation",
                             requested_data=["rubric"], use_active_documents=followup)
            with patch.object(canvas_service, "get_assignment") as live:
                result = self.route(q, plan, context)
            live.assert_not_called()
            self.assertEqual(result["retrieval_route"], "saved_assignment_rubric")
            self.assertEqual(result["resolved_assignments"], [201])
            self.assertEqual([r["structured_data"]["points"] for r in result["results"]], [24, 24, 24, 28])
            self.assertEqual([r["structured_data"]["id"] for r in result["results"]], ["c0", "c1", "c2", "c3"])
        self.assertFalse(self.embedding.calls)

    def test_document_and_focused_question_routes_preserve_numbers(self):
        for q, intent, ref, mode in (("explain Practice Questions 10", "explain_document", "Practice Questions 10", "overview"),
                                   ("summarize Practice Questions 10", "summarize_document", "Practice Questions 10", "overview"),
                                   ("explain question 7 from Practice Questions 10", "explain_question", "Practice Questions 10", "focused"),
                                   ("explain Practice Questions 1", "explain_document", "Practice Questions 1", "overview")):
            result = self.route(q, self.plan(q, intent, document_reference=ref, question_number="7" if "question 7" in q else ""))
            self.assertEqual(result["retrieval_mode"], mode)
            self.assertEqual(result["resolved_documents"], ["file:1" if ref.endswith(" 1") else "file:10"])

    def test_homework_10_never_selects_homework_1(self):
        q = "explain homework 10"
        self.assertEqual(self.route(q, self.plan(q, "explain_assignment", assignment_reference="Homework 10"))["resolved_assignments"], [110])

    def test_model_changed_number_is_rejected_before_retrieval(self):
        result = self.route("explain homework 1", self.plan("explain homework 10", "explain_assignment", assignment_reference="Homework 10"))
        self.assertEqual(result["notice_status"], "clarification_needed")
        self.assertFalse(self.embedding.calls)

    def test_ambiguous_iteration_and_missing_rubric_do_not_use_general_advice(self):
        result = self.route("explain Iteration 3 rubric", self.plan("Iteration 3", "explain_rubric", assignment_reference="Iteration 3"))
        self.assertEqual(result["notice_status"], "clarification_needed")
        result = self.route("explain the rubric for HW 1", self.plan("HW 1", "explain_rubric", assignment_reference="HW1"))
        self.assertEqual(result["results"], [])
        self.assertIn("did not provide", result["notice"])

    def test_course_reference_cannot_override_selected_course(self):
        result = self.route("CSE 4321 rubric", self.plan("rubric", "explain_rubric", course_reference="CSE 4321", assignment_reference="Iteration 3 Presentation"))
        self.assertEqual(result["notice_status"], "clarification_needed")

    def test_solution_intent_is_separate_from_explanation(self):
        q = "show me the solution for HW 1"
        result = self.route(q, self.plan(q, "solve_question", assignment_reference="HW1"))
        keys = {o["document_key"] for r in result["results"] for o in r["sources"]}
        self.assertEqual(keys, {"assignment:101", "file:502"})

    def test_study_plan_is_recognized_but_does_not_invent_a_schedule(self):
        result = self.route("make a study plan", self.plan("make a study plan", "study_plan"))
        self.assertEqual(result["retrieval_route"], "study_plan_unavailable")
        self.assertFalse(result["results"] or self.embedding.calls)

    def test_submission_state_and_points_use_fresh_fields(self):
        for intent, requested in (("get_submission_status", ["submission_status"]), ("get_assignment_details", ["points"])):
            with patch.object(canvas_service, "get_assignment", return_value=self.assignments[0]["metadata"]):
                result = self.route("HW1 current facts", self.plan("HW1", intent, assignment_reference="HW1", requested_data=requested))
            self.assertTrue(result["direct_response"])
        self.assertFalse(self.embedding.calls)

    def test_live_failure_has_no_stale_fact_fallback(self):
        with patch.object(canvas_service, "get_assignment", side_effect=requests.ConnectionError("private upstream")), self.assertRaises(LLMProviderError) as caught:
            self.route("when is HW1 due", self.plan("HW1 due", "get_deadline", assignment_reference="HW1"))
        self.assertNotIn("private", str(caught.exception))

    def test_weekly_deadline_lookup_and_empty_live_list_are_explicit(self):
        with patch.object(canvas_service, "get_assignments", return_value=[]):
            result = self.route("what assignments are due this week", self.plan("assignments due this week", "get_deadline", deadline_reference="this week"))
        self.assertTrue(result["direct_response"])
        self.assertEqual(result["results"][0]["sources"][0]["source_type"], "assignment_collection")
        self.assertIn("No Canvas assignments", result["results"][0]["text"])

    def test_missing_explicit_document_never_falls_back_to_global_vectors(self):
        result = self.route("explain Missing.pdf", self.plan("explain Missing.pdf", "explain_document", document_reference="Missing.pdf"))
        self.assertFalse(result["results"] or self.embedding.calls)

    def test_unrelated_active_document_does_not_select_a_random_available_rubric(self):
        result = self.route("how is this graded", self.plan("how is this graded", "explain_rubric", use_active_documents=True),
                            {"active_documents": [{"document_key": "file:10"}], "active_assignments": []})
        self.assertEqual(result["notice_status"], "clarification_needed")

    def test_rubric_lookup_cannot_read_another_account_snapshot(self):
        other = IngestionStore("other", self.store.path)
        other.save_course({"course_id": 42, "synced_at": "now", "assignments": [], "course": {"metadata": {"course_code": "CSE 3311"}}})
        result = route_query(42, "Iteration 3 Presentation rubric", self.plan("Iteration 3 Presentation", "explain_rubric",
                             assignment_reference="Iteration 3 Presentation"), store=other, provider=self.embedding)
        self.assertFalse(result["results"])


class RoutedGenerationTests(RoutingFixture, unittest.TestCase):
    def test_rubric_reaches_answer_model_with_exact_ratings_and_valid_citations(self):
        q = "how is Iteration 3 Presentation graded"
        provider = PlannedProvider(self.plan(q, "explain_rubric", assignment_reference="Iteration 3 Presentation", requested_data=["rubric"]))
        result = rag_service.answer_question(42, q, store=self.store, embedding_provider=self.embedding, llm_provider=provider)
        self.assertEqual(len(provider.understanding_calls), 1)
        self.assertEqual(len(provider.answer_calls), 1)
        self.assertEqual(len(provider.answer_calls[0][3]), 4)
        self.assertTrue(all(p["source_kind"] == "structured_canvas" for p in provider.answer_calls[0][3]))
        self.assertIn('"points": 28', provider.answer_calls[0][3][-1]["text"])
        self.assertEqual(result["answer_blocks"][0]["source_ids"], ["S1", "S2", "S3", "S4"])
        self.assertFalse(self.embedding.calls)

    def test_current_facts_do_not_call_answer_model(self):
        q = "when is HW6 due"
        provider = PlannedProvider(self.plan(q, "get_deadline", assignment_reference="HW6"))
        with patch.object(canvas_service, "get_assignment", return_value=self.assignments[2]["metadata"]):
            result = rag_service.answer_question(42, q, store=self.store, embedding_provider=self.embedding, llm_provider=provider)
        self.assertEqual(len(provider.understanding_calls), 1)
        self.assertFalse(provider.answer_calls or self.embedding.calls)
        self.assertIn("2026-11-02 23:59 CST", result["answer"])
        self.assertEqual(result["status"], "answered")

    def test_understanding_catalog_contains_titles_but_not_submission_payloads(self):
        q = "how is Iteration 3 Presentation graded"
        provider = PlannedProvider(self.plan(q, "explain_rubric", assignment_reference="Iteration 3 Presentation"))
        rag_service.answer_question(42, q, store=self.store, embedding_provider=self.embedding, llm_provider=provider)
        context = provider.understanding_calls[0][2]
        self.assertIn("Iteration 3 Presentation", context["assignment_titles"])
        self.assertNotIn("submission", json.dumps(context))

    def test_semantic_solution_intent_drives_answer_mode_without_keyword_dependency(self):
        self.assertIn("CURRENT REQUEST MODE\nSOLUTION:", rag_service.answer_instructions(
            "work it out for me", self.plan("work it out", "solve_question")))


class UnderstandingProviderTests(unittest.IsolatedAsyncioTestCase):
    async def test_existing_rewrite_call_uses_richer_strict_schema_once(self):
        value = empty_understanding("Iteration 3 grading", intent="explain_rubric", assignment_reference="Iteration 3 Presentation", requested_data=["rubric"])
        with patch("openai.AsyncOpenAI") as factory:
            from unittest.mock import AsyncMock
            client = factory.return_value.__aenter__.return_value
            client.responses.create = AsyncMock(return_value=SimpleNamespace(status="completed", output_text=json.dumps(value)))
            result = await OpenAIAnswerProvider(api_key="fake-key", usage_recorder=False).rewrite_query(42, "how is Iteration 3 graded", {})
            self.assertEqual(result, value)
            self.assertEqual(client.responses.create.await_count, 1)
            kwargs = client.responses.create.call_args.kwargs
            self.assertFalse(kwargs["store"])
            self.assertEqual(kwargs["text"]["format"]["schema"], query_schema())
            self.assertNotIn("fake-key", kwargs["input"])

    async def test_malformed_understanding_is_a_safe_provider_error(self):
        with patch("openai.AsyncOpenAI") as factory:
            from unittest.mock import AsyncMock
            factory.return_value.__aenter__.return_value.responses.create = AsyncMock(
                return_value=SimpleNamespace(status="completed", output_text='{"assignment_id": 999}'))
            with self.assertRaises(LLMProviderError):
                await OpenAIAnswerProvider(api_key="fake-key", usage_recorder=False).rewrite_query(42, "Question", {})


class RoutedConversationTests(RoutingFixture, unittest.IsolatedAsyncioTestCase):
    async def test_missing_new_topic_clears_previous_assignment_focus(self):
        sessions = ConversationService()
        provider = PlannedProvider(self.plan("HW1", "explain_assignment", assignment_reference="HW1"))
        session = sessions.acquire("student", 42)
        async for _ in sessions.events(session, "explain HW1", "1", store=self.store, provider=provider, embedding_provider=self.embedding):pass
        self.assertEqual(session.active_assignments, [101])
        provider.plan = self.plan("HW999", "explain_assignment", assignment_reference="HW999")
        session = sessions.acquire("student", 42, session.id)
        events = [e async for e in sessions.events(session, "explain HW999", "2", store=self.store, provider=provider, embedding_provider=self.embedding)]
        self.assertEqual(events[-1]["data"]["status"], "insufficient_context")
        self.assertFalse(session.active_assignments or session.active_documents)
        self.assertEqual(session.history[-1]["question"], "explain HW999")

    async def test_rubric_followup_keeps_native_assignment_even_without_document_text(self):
        self.store.save_document(42, {"document_key": "assignment:201", "source_type": "assignment", "title": "Iteration 3 Presentation",
                                      "status": "no_text", "text": ""}, self.snapshot["synced_at"])
        sessions = ConversationService()
        provider = PlannedProvider(self.plan("Iteration 3 Presentation rubric", "explain_rubric", assignment_reference="Iteration 3 Presentation"))
        session = sessions.acquire("student", 42)
        first = [e async for e in sessions.events(session, "how is Iteration 3 Presentation graded", "1", store=self.store, provider=provider, embedding_provider=self.embedding)]
        self.assertEqual(first[-1]["data"]["status"], "answered")
        self.assertEqual(session.active_assignments, [201])
        provider.plan = self.plan("Iteration 3 Presentation criterion points", "explain_rubric", use_active_documents=True)
        session = sessions.acquire("student", 42, session.id)
        second = [e async for e in sessions.events(session, "how many points is each criterion worth", "2", store=self.store, provider=provider, embedding_provider=self.embedding)]
        self.assertEqual(second[-1]["data"]["resolved_assignments"], [201])
        self.assertEqual(len(provider.understanding_calls), 2)
        self.assertFalse(self.embedding.calls)


class AdditionalRoutingTests(RoutingFixture, unittest.TestCase):
    def test_live_deadlines_sort_by_aware_datetimes_not_offset_strings(self):
        records = [{**self.assignments[0]["metadata"], "due_at": "2026-11-02T00:30:00-06:00"},
                   {**self.assignments[2]["metadata"], "due_at": "2026-11-02T06:00:00Z"}]
        with patch.object(canvas_service, "get_assignments", return_value=records):
            result = self.route("what is due tomorrow", self.plan("due tomorrow", "get_deadline", deadline_reference="tomorrow"))
        self.assertEqual(result["resolved_assignments"], [106, 101])

    def test_refreshing_course_does_not_report_known_files_as_missing(self):
        self.store.save_course({**self.snapshot, "ingestion_status": "running"})
        result = self.route("explain Practice Questions 10", self.plan("Practice Questions 10", "explain_document", document_reference="Practice Questions 10"))
        self.assertEqual(result["notice_status"], "indexing_needed")
        self.assertIn("being refreshed", result["notice"])
        self.assertFalse(self.embedding.calls)

    def test_structured_rubric_remains_available_during_document_refresh(self):
        self.store.save_course({**self.snapshot, "ingestion_status": "running"})
        result = self.route("Iteration 3 Presentation rubric", self.plan("Iteration 3 Presentation", "explain_rubric", assignment_reference="Iteration 3 Presentation"))
        self.assertEqual(result["retrieval_route"], "saved_assignment_rubric")
        self.assertEqual(len(result["results"]), 4)

    def test_model_changed_question_number_is_rejected(self):
        result = self.route("explain question 7 from Practice Questions 10", self.plan("question 8", "explain_question",
                            document_reference="Practice Questions 10", question_number="8"))
        self.assertEqual(result["notice_status"], "clarification_needed")

    def test_model_changed_iteration_number_is_rejected(self):
        result = self.route("Iteration 3 Presentation rubric", self.plan("Iteration 4 Presentation", "explain_rubric",
                            assignment_reference="Iteration 4 Presentation"))
        self.assertEqual(result["notice_status"], "clarification_needed")

    def test_pronoun_deadline_lookup_preserves_active_assignment(self):
        context = {"active_assignments": [{"assignment_id": 101}], "active_documents": []}
        with patch.object(canvas_service, "get_assignment", return_value=self.assignments[0]["metadata"]) as single, patch.object(canvas_service, "get_assignments") as listing:
            result = self.route("when is it due tomorrow", self.plan("HW1 deadline", "get_deadline", deadline_reference="tomorrow",
                                use_active_documents=True), context)
        single.assert_called_once_with(42, 101);listing.assert_not_called()
        self.assertEqual(result["resolved_assignments"], [101])

    def test_structured_sources_from_another_course_are_rejected_before_generation(self):
        result = self.route("Iteration 3 Presentation rubric", self.plan("Iteration 3 Presentation", "explain_rubric", assignment_reference="Iteration 3 Presentation"))
        for item in result["results"]:
            for origin in item["sources"]:origin["course_id"] = 99
        report, context, _ = rag_service._prepare_context(42, result)
        self.assertEqual(report["status"], "insufficient_context")
        self.assertFalse(context)
        self.assertNotIn("structured_data", report)

    def test_due_modifier_generation_is_an_explanation_not_a_structured_fact_deferral(self):
        q = "what is being asked in homework 6 that is due"
        provider = PlannedProvider(self.plan(q, "explain_assignment", assignment_reference="HW6"))
        result = rag_service.answer_question(42, q, store=self.store, embedding_provider=self.embedding, llm_provider=provider)
        self.assertEqual(result["status"], "answered")
        self.assertIn("do not defer merely", provider.answer_calls[0][0])
        self.assertTrue(any("parser" in p["text"] for p in provider.answer_calls[0][3]))


class AdditionalStructuredDataTests(unittest.TestCase):
    def test_individual_assignment_request_reuses_canvas_auth_and_closes_response(self):
        response = Mock();response.json.return_value = {"id": 7}
        with patch.object(canvas_service, "CANVAS_URL", "https://canvas.example"), patch.object(canvas_service, "_request_get", return_value=response) as get:
            self.assertEqual(canvas_service.get_assignment(42, 7), {"id": 7})
        self.assertEqual(get.call_args.args, ("https://canvas.example/api/v1/courses/42/assignments/7",))
        self.assertEqual(get.call_args.kwargs["params"], {"include[]": "submission"})
        self.assertIs(get.call_args.kwargs["headers"], canvas_service.headers)
        response.close.assert_called_once()

    def test_canvas_rubric_can_have_no_rating_scale_and_still_preserve_criteria(self):
        record = assignment(1, "Presentation", "", rubric=[{"id": "c", "description": "Content", "points": 20, "ratings": None}])
        result = rubric_evidence(42, record, "saved")
        self.assertIsNone(result[0]["structured_data"]["ratings"])
        self.assertEqual(result[0]["structured_data"]["points"], 20)


if __name__ == "__main__":unittest.main()
