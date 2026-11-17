"""EC-016: recoverable optional-field variations in a model plan no longer fail the whole request.

Stubbed provider responses, synthetic fixtures and temporary SQLite only. The
two "live shape" plans mirror the questions that failed in live verification:
a plan valid in every respect except a number field the schema types as text.
"""

import asyncio
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from backend.app import rag_service
from backend.app.chunking_service import ensure_course_chunks
from backend.app.conversation_service import ConversationService
from backend.app.embedding_service import embed_course
from backend.app.llm_service import LLMProviderError, OpenAIAnswerProvider
from backend.app.query_understanding_service import (
    PlanRejected, checked_understanding, empty_understanding, repair_understanding, validate_understanding,
)
from backend.evaluation.fixtures import EvaluationFixture, ReplayProvider

LOGGER = "backend.app.query_understanding_service"
FIRST_QUESTION = "Solve the first question of HW 1."
DIGIT_LIST = "Solve this practice problem: evaluate (car (cdr '(1 2 3)))."
UNTOUCHED = ("intent", "operation", "entity_type", "course_reference", "assignment_reference", "document_reference",
             "part", "deadline_reference", "clarification", "requires_live_canvas", "use_active_documents")


def plan(question="Explain HW 1", **fields):
    return empty_understanding(question, **fields)


def records(captured):
    return [json.loads(line.split("query_plan_validation ", 1)[1]) for line in captured.output]


class RepairTests(unittest.TestCase):
    def test_number_stated_once_is_normalized(self):
        for written, expected in (("first", "1"), ("Second", "2"), ("Q1", "1"), ("#2", "2"), (" 3 ", "3"), ("3.", "3"),
                                  ("question 4", "4"), ("1a", "1"), ("1st", "1"), ("３", "3"), ("six", "6"),
                                  ("the first one", "1"), ("07", "07"), ("12", "12")):
            with self.subTest(written=written):
                repaired, repairs = repair_understanding(plan(question_number=written), "q")
                self.assertEqual(repaired["question_number"], expected)
                self.assertEqual(bool(repairs), written not in ("07", "12"))        # valid digits are left alone
                validate_understanding(repaired)

    def test_unclear_question_number_is_dropped_not_guessed(self):
        for written in ("1 2 3", "first two", "all", "last", "3.5", "1 or 2", "the big one", "2nd one", "question 2 the first part"):
            with self.subTest(written=written):
                repaired, repairs = repair_understanding(plan(question_number=written), "q")
                self.assertEqual(repaired["question_number"], "")
                self.assertEqual(repairs[0]["condition"], "unclear_number_dropped")
                validate_understanding(repaired)

    def test_blank_items_duplicates_and_empty_query_are_repaired(self):
        raw = plan(included_references=["HW 1", " ", ""], excluded_references=["", "HW 6"],
                   requested_data=["question", "solution", "question"], query="   ")
        repaired, repairs = repair_understanding(raw, "  Explain HW 1  ")
        self.assertEqual(repaired["included_references"], ["HW 1"])
        self.assertEqual(repaired["excluded_references"], ["HW 6"])
        self.assertEqual(repaired["requested_data"], ["question", "solution"])
        self.assertEqual(repaired["query"], "Explain HW 1")                       # the student's own words
        self.assertEqual({r["condition"] for r in repairs}, {"blank_items_removed", "duplicates_removed", "empty_query_replaced"})
        validate_understanding(repaired)

    def test_repair_never_changes_identity_intent_or_flags(self):
        for raw in (plan(intent="solve_question", assignment_reference="HW 6", question_number="first", homework_number="six or ten",
                         course_reference="CSE TEST", document_reference="HW6_questions.pdf", part="b", deadline_reference="tomorrow",
                         operation="continue", use_active_documents=True, requires_live_canvas=True, clarification="Which one?"),
                    plan(intent="general_concept", question_number="1 2 3", included_references=[""]),
                    plan(intent="get_deadline", assignment_number="Q9", entity_type="homework")):
            repaired, _ = repair_understanding(raw, "q")
            with self.subTest(intent=raw["intent"]):
                self.assertEqual({key: repaired[key] for key in UNTOUCHED}, {key: raw[key] for key in UNTOUCHED})

    def test_valid_plan_is_returned_unchanged_and_silently(self):
        raw = plan(intent="solve_question", assignment_reference="HW 6", question_number="2")
        with self.assertNoLogs(LOGGER):
            self.assertEqual(checked_understanding(raw, "Solve HW 6 question 2"), raw)


class StrictValidationTests(unittest.TestCase):
    """Repair is not leniency: required, identity and contradictory fields are still rejected."""

    def rejected(self, raw, field, question="q"):
        with self.assertLogs(LOGGER) as captured, self.assertRaises(PlanRejected) as raised:
            checked_understanding(raw, question)
        self.assertEqual(raised.exception.field, field)
        self.assertEqual([(r["outcome"], r["field"]) for r in records(captured)], [("rejected", field)])

    def test_required_and_contract_fields_are_still_rejected(self):
        missing = plan()
        del missing["intent"]
        self.rejected(missing, "plan")
        self.rejected({**plan(), "assignment_id": 101}, "plan")
        self.rejected("not a plan", "plan")
        self.rejected(plan(intent="reveal_secrets"), "intent")
        self.rejected(plan(operation="delete"), "operation")
        self.rejected(plan(entity_type="exam"), "entity_type")
        self.rejected(plan(requested_data=["grades_of_other_students"]), "requested_data")
        self.rejected(plan(use_active_documents="yes"), "use_active_documents")
        self.rejected(plan(requires_live_canvas=1), "requires_live_canvas")
        self.rejected(plan(question_number=7), "question_number")
        self.rejected(plan(query="   "), "query", question="  ")                    # nothing to fall back to

    def test_identity_references_are_never_repaired(self):
        self.rejected(plan(assignment_reference="x" * 601), "assignment_reference")
        self.rejected(plan(course_reference="x" * 601), "course_reference")
        self.rejected(plan(document_reference=["HW6.pdf"]), "document_reference")
        self.rejected(plan(excluded_references=["x" * 601]), "excluded_references")  # dropping it would invert an exclusion
        self.rejected(plan(included_references=["HW"] * 21), "included_references")

    def test_unclear_assignment_number_without_a_named_assignment_is_rejected(self):
        # The number is the only identity the plan carries; dropping it would turn the request course-wide.
        self.rejected(plan(intent="solve_question", homework_number="6 or 10"), "homework_number")
        self.rejected(plan(intent="explain_assignment", assignment_number="the big one"), "assignment_number")
        repaired, repairs = repair_understanding(plan(assignment_reference="HW 6", homework_number="6 or 10"), "q")
        self.assertEqual((repaired["homework_number"], repaired["assignment_reference"]), ("", "HW 6"))
        self.assertEqual(repairs[0]["condition"], "unclear_number_dropped")

    def test_contradictory_general_concept_plan_is_rejected(self):
        # A general concept has no question number; dropping it would open the ungrounded route.
        self.rejected(plan(intent="general_concept", question_number="1 2 3"), "question_number")


class DiagnosticLoggingTests(unittest.TestCase):
    SECRET_QUESTION = "Solve the first question of my Operating Systems homework for Jordan Example"

    def test_repair_and_rejection_logs_hold_shapes_never_values(self):
        raw = plan(self.SECRET_QUESTION, intent="solve_question", assignment_reference="Operating Systems Homework 4",
                   course_reference="CSE 9999", question_number="the very first one", included_references=["Private Title", ""])
        with self.assertLogs(LOGGER) as captured:
            checked_understanding(raw, self.SECRET_QUESTION, model="test-model")
            with self.assertRaises(PlanRejected):
                checked_understanding(plan(self.SECRET_QUESTION, homework_number="Jordan's 3rd or 4th"), self.SECRET_QUESTION)
        text = "\n".join(captured.output)
        for private in (self.SECRET_QUESTION, "Jordan", "Operating Systems", "CSE 9999", "Private Title", "the very first one", "3rd"):
            self.assertNotIn(private, text)
        logged = records(captured)
        self.assertEqual([(r["outcome"], r["field"], r["condition"]) for r in logged],
                         [("repaired", "question_number", "number_normalized"), ("repaired", "included_references", "blank_items_removed"),
                          ("rejected", "homework_number", "not_digits")])
        self.assertEqual(logged[0]["shape"], {"type": "text", "bytes": 18, "contains": ["letters", "spaces"], "digit_groups": 0})
        self.assertEqual((logged[0]["intent"], logged[0]["model"]), ("solve_question", "test-model"))
        self.assertTrue(all(set(r) <= {"outcome", "field", "condition", "shape", "intent", "model"} for r in logged))


class ProviderTests(unittest.TestCase):
    """The real provider method, with only the network client stubbed."""

    def rewrite(self, raw=None, *, status="completed", error=None, question=FIRST_QUESTION):
        response = SimpleNamespace(id="r", status=status, output_text=json.dumps(raw) if raw is not None else "not json",
                                   usage=SimpleNamespace(input_tokens=10, output_tokens=5),
                                   incomplete_details=SimpleNamespace(reason="max_output_tokens"))
        with patch("openai.AsyncOpenAI") as factory:
            factory.return_value.__aenter__.return_value.responses.create = AsyncMock(return_value=response, side_effect=error)
            provider = OpenAIAnswerProvider(api_key="fake", usage_recorder=None)
            return asyncio.run(provider.rewrite_query(42, question, {}))

    def test_live_shape_plans_are_repaired_by_the_provider(self):
        with self.assertLogs(LOGGER) as captured:
            first = self.rewrite(plan(FIRST_QUESTION, intent="solve_question", assignment_reference="HW 1", question_number="first"))
            digits = self.rewrite(plan(DIGIT_LIST, intent="solve_question", question_number="1 2 3"), question=DIGIT_LIST)
        self.assertEqual((first["question_number"], first["assignment_reference"]), ("1", "HW 1"))
        self.assertEqual(digits["question_number"], "")
        self.assertEqual([r["condition"] for r in records(captured)], ["number_normalized", "unclear_number_dropped"])

    def test_rejections_and_provider_failures_stay_errors_and_are_logged_safely(self):
        with self.assertLogs(LOGGER) as captured:
            with self.assertRaises(LLMProviderError):
                self.rewrite(plan(intent="solve_question", homework_number="6 or 10"))
            with self.assertRaises(LLMProviderError):
                self.rewrite(plan(), status="incomplete")
            with self.assertRaises(LLMProviderError):
                self.rewrite(None)                                                   # unparseable output
            with self.assertRaises(LLMProviderError):
                self.rewrite(plan(), error=TypeError("request for " + FIRST_QUESTION + " failed with key sk-secret"))
        logged = records(captured)
        self.assertEqual([(r["outcome"], r.get("field"), r["condition"]) for r in logged],
                         [("rejected", "homework_number", "not_digits"), ("incomplete", None, "incomplete:max_output_tokens"),
                          ("failed", None, "JSONDecodeError"), ("failed", None, "TypeError")])
        self.assertNotIn("sk-secret", "\n".join(captured.output))                     # error class only, never its message
        self.assertNotIn(FIRST_QUESTION, "\n".join(captured.output))


class RoutingSafetyTests(unittest.TestCase):
    """A repaired plan reaches the same policy and grounding decisions as a well-formed one (EC-015)."""

    COURSE_BAN = "Course policies. Use of generative AI tools is prohibited in this course."

    def setUp(self):
        self.fixture = EvaluationFixture()
        self.addCleanup(self.fixture.close)

    def syllabus(self):
        self.fixture.save("syllabus:42", "Syllabus", self.COURSE_BAN, source_type="syllabus")
        ensure_course_chunks(42, self.fixture.store)
        embed_course(42, store=self.fixture.store, provider=self.fixture.embedding)

    def ask(self, question, **fields):
        provider = ReplayProvider(plan(question, **fields))
        with self.assertLogs(LOGGER):
            result = rag_service.answer_question(42, question, store=self.fixture.store, embedding_provider=self.fixture.embedding,
                                                 llm_provider=provider)
        return result, provider

    def test_assignment_ban_survives_every_repair(self):
        for fields in (dict(assignment_reference="HW 6", question_number="first"),          # number normalized
                       dict(assignment_reference="HW 6", question_number="1 2 3"),          # question number dropped
                       dict(assignment_reference="HW 6", homework_number="six or ten"),     # redundant number dropped
                       dict(homework_number="six"),                                         # the only identity, normalized
                       dict(assignment_reference="HW 6", included_references=["", "HW 6"], requested_data=["solution", "solution"])):
            with self.subTest(fields=fields):
                result, provider = self.ask("Solve Homework 6 for me", intent="solve_question", **fields)
                self.assertEqual(result["resolved_assignments"], [106])
                self.assertTrue(result["policy_enforced"])
                self.assertFalse(provider.contexts)                                        # refused before generation

    def test_course_wide_ban_survives_a_dropped_question_number(self):
        self.syllabus()
        result, provider = self.ask("Solve the first two questions of Homework 1", intent="solve_question",
                                    assignment_reference="HW 1", question_number="first two")
        self.assertTrue(result["policy_enforced"])
        self.assertFalse(provider.contexts)

    def test_unrecoverable_identity_is_an_error_not_a_course_wide_answer(self):
        provider = ReplayProvider(plan("Solve homework six or ten", intent="solve_question", homework_number="6 or 10"))
        with self.assertLogs(LOGGER), self.assertRaises(PlanRejected):
            rag_service.answer_question(42, "Solve homework six or ten", store=self.fixture.store,
                                        embedding_provider=self.fixture.embedding, llm_provider=provider)
        self.assertFalse(provider.contexts)

    def test_contradictory_general_plan_never_reaches_the_ungrounded_route(self):
        provider = ReplayProvider(plan("What is recursion?", intent="general_concept", question_number="1 2 3"))
        with self.assertLogs(LOGGER), self.assertRaises(PlanRejected):
            rag_service.answer_question(42, "What is recursion?", store=self.fixture.store,
                                        embedding_provider=self.fixture.embedding, llm_provider=provider)

    def test_normalized_number_that_contradicts_the_student_is_clarified(self):
        # The existing cross-check still applies to a repaired value.
        result, provider = self.ask("Explain question 2 of HW 1", intent="explain_question", assignment_reference="HW 1", question_number="Q3")
        self.assertEqual(result["status"], "clarification_needed")
        self.assertFalse(provider.contexts)


class ConversationTests(unittest.IsolatedAsyncioTestCase):
    async def turn(self, question, **fields):
        fixture = EvaluationFixture()
        self.addCleanup(fixture.close)
        raw = plan(question, **fields)
        response = SimpleNamespace(id="r", status="completed", output_text=json.dumps(raw), usage=SimpleNamespace(input_tokens=10, output_tokens=5))
        with patch("openai.AsyncOpenAI") as factory:
            factory.return_value.__aenter__.return_value.responses.create = AsyncMock(return_value=response)
            provider = OpenAIAnswerProvider(api_key="fake", usage_recorder=None)
            provider.stream_generate = ReplayProvider(raw).stream_generate           # generation stays a declared replay
            service = ConversationService(repository=None)
            session = service.acquire(fixture.store.namespace, 42)
            return [event async for event in service.events(session, question, "1", store=fixture.store, provider=provider,
                                                             embedding_provider=fixture.embedding)]

    async def test_the_two_live_failures_now_answer(self):
        for question, fields, number in ((FIRST_QUESTION, dict(assignment_reference="HW 1", question_number="first"), "1"),
                                         (DIGIT_LIST, dict(question_number="1 2 3"), "")):
            with self.subTest(question=question), self.assertLogs(LOGGER):
                events = await self.turn(question, intent="solve_question", **fields)
                self.assertEqual(events[-1]["event"], "final", events[-1])          # was: error, "could not complete a validated response"
                self.assertEqual(events[-1]["data"]["status"], "answered")
                self.assertEqual(events[-1]["data"]["query_understanding"]["question_number"], number)

    async def test_rejected_plan_still_ends_in_the_safe_error_message(self):
        with self.assertLogs(LOGGER):
            events = await self.turn("Solve homework six or ten", intent="solve_question", homework_number="6 or 10")
        self.assertEqual([event["event"] for event in events], ["session", "searching", "error"])
        self.assertEqual(events[-1]["data"]["message"], "The assistant could not complete a validated response. Please retry.")


if __name__ == "__main__":
    unittest.main()
