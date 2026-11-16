"""EC-014: deterministic conversation state overrides an unsupported continuation claim.

Fake providers and temporary SQLite only. The first-turn plan below is the exact
shape the live query model produced in the EC-013 verification run.
"""

import unittest

from backend.app.chunking_service import ensure_course_chunks
from backend.app.conversation_service import ConversationError
from backend.app.conversation_state_service import reject_unsupported_continuation
from backend.app.embedding_service import embed_course
from backend.app.query_understanding_service import empty_understanding
from backend.tests import test_general_concept_route as general_route
from backend.tests.test_general_concept_route import ConversationProvider
from backend.tests.test_query_routing import RoutingFixture

STANDALONE = "What are the advantages and disadvantages of FCFS scheduling?"


def continuation_claim(question, intent="course_wide_question", **changes):
    """The live model's first-turn shape: continue + active documents + a course reference."""
    return empty_understanding(question, intent=intent, operation="continue", use_active_documents=True, **changes)


class ContinuationStateTests(unittest.TestCase):
    def test_claim_is_rejected_only_without_conversation_state(self):
        plan = continuation_claim(STANDALONE, course_reference="Software Engineering")
        corrected = reject_unsupported_continuation(plan, {})
        self.assertEqual((corrected["operation"], corrected["use_active_documents"]), ("topic_switch", False))
        self.assertEqual(corrected["course_reference"], "Software Engineering")  # references untouched here
        for context in ({"recent_turns": [{"question": "q", "answer": "a"}]},
                        {"active_documents": [{"document_key": "file:1"}]},
                        {"active_assignments": [{"assignment_id": 101}]},
                        {"pending_clarification": {"candidates": [{"assignment_id": 101}]}}):
            with self.subTest(context=sorted(context)):
                self.assertEqual(reject_unsupported_continuation(plan, context), plan)

    def test_other_operations_are_not_rewritten(self):
        plan = empty_understanding("assignments other than HW1", intent="list_assignments", operation="exclude",
                                   excluded_references=["HW1"])
        self.assertEqual(reject_unsupported_continuation(plan, {}), plan)


class ContinuationRoutingTests(RoutingFixture, unittest.IsolatedAsyncioTestCase):
    # Borrow helpers by module reference so the borrowed TestCase is not collected twice.
    asyncSetUp = general_route.GeneralConceptStreamTests.asyncSetUp
    turn = general_route.GeneralConceptStreamTests.turn

    async def ask(self, course_id, question, plan, conversation_id=None):
        provider = ConversationProvider(plan)
        session = self.service.acquire(self.store.namespace, course_id, conversation_id)
        events = [event async for event in self.service.events(session, question, str(len(session.turns) + 1),
                                                                store=self.store, provider=provider,
                                                                embedding_provider=self.embedding)]
        self.assertEqual(events[-1]["event"], "final", events[-1])
        return events[-1]["data"], provider, session

    async def test_first_turn_continuation_claim_becomes_a_standalone_question(self):
        # Live shape: model-added full course name the student never wrote.
        plan = continuation_claim(STANDALONE, course_reference="CSE 3311 Software Engineering")
        result, provider = await self.turn(STANDALONE, plan)
        understood = result["query_understanding"]
        self.assertEqual((understood["operation"], understood["use_active_documents"]), ("topic_switch", False))
        self.assertEqual((result["retrieval_route"], result["answer_basis"]), ("general_concept", "general_knowledge"))
        self.assertEqual(result["sources"], [])
        self.assertEqual(provider.general_contexts, [{}])     # no invented conversation context

    def test_stateless_route_rejects_the_claim_too(self):
        result = self.route(STANDALONE, continuation_claim(STANDALONE, course_reference="CSE 3311"))
        self.assertEqual(result["retrieval_route"], "general_concept")

    async def test_genuine_followup_keeps_its_context(self):
        await self.turn("Explain HW1", empty_understanding("Explain HW1", intent="explain_assignment", assignment_reference="HW1"))
        result, _ = await self.turn("What about part b?", continuation_claim(
            "What about part b?", intent="explain_question", part="b", course_reference="CSE 3311 Software Engineering"))
        self.assertEqual(result["query_understanding"]["operation"], "continue")
        self.assertTrue(result["query_understanding"]["use_active_documents"])
        self.assertEqual((result["retrieval_route"], result["resolved_assignments"]), ("assignment_material", [101]))

    async def test_first_turn_explicit_course_requests_keep_the_students_reference(self):
        for question, reference in (("According to my CSE 3311 slides, what is orthogonal projection?", "CSE 3311"),
                                    ("What is orthogonal projection in CSE 3311?", "CSE 3311")):
            with self.subTest(question=question):
                self.session = None
                result, provider = await self.turn(question, continuation_claim(question, course_reference=reference))
                self.assertEqual(result["query_understanding"]["course_reference"], reference)
                self.assertEqual(result["query_understanding"]["operation"], "topic_switch")
                self.assertEqual(result["retrieval_route"], "course_material")
                self.assertTrue(result["sources"])
                self.assertIsNone(result.get("answer_basis"))
                self.assertFalse(provider.general_contexts)

    async def test_course_switch_starts_without_the_previous_courses_context(self):
        self.store.save_course({"course_id": 43, "synced_at": self.snapshot["synced_at"],
                                "course": {"metadata": {"course_code": "CSE 3320", "name": "Operating Systems"}}})
        self.store.save_document(43, {"document_key": "file:900", "canvas_id": 900, "course_id": 43, "source_type": "file",
                                      "title": "Scheduling.pdf", "status": "ok",
                                      "text": "FCFS runs processes in arrival order; short jobs can wait behind long ones."},
                                 self.snapshot["synced_at"])
        ensure_course_chunks(43, self.store)
        embed_course(43, store=self.store, provider=self.embedding)
        first, _, course_42 = await self.ask(42, "Explain HW1", empty_understanding(
            "Explain HW1", intent="explain_assignment", assignment_reference="HW1"))
        self.assertTrue(course_42.active_documents)
        with self.assertRaises(ConversationError):         # a conversation never crosses courses
            self.service.acquire(self.store.namespace, 43, course_42.id)
        question = "Explain that in more detail using the course notes"
        result, _, course_43 = await self.ask(43, question, continuation_claim(question, intent="explain_question",
                                                                               document_reference="course notes"))
        self.assertEqual(result["query_understanding"]["operation"], "topic_switch")
        self.assertEqual(result["retrieval_route"], "course_material")
        cited = {origin["document_key"] for source in result["sources"] for origin in source["origins"]}
        self.assertEqual(cited, {"file:900"})
        self.assertTrue(all(origin["course_id"] == 43 for source in result["sources"] for origin in source["origins"]))

    async def test_failed_retrieval_after_a_rejected_claim_never_uses_general_knowledge(self):
        with self.store._connection() as connection:
            connection.execute("DELETE FROM chunk_embeddings")
        question = "According to my CSE 3311 slides, what is orthogonal projection?"
        result, provider = await self.turn(question, continuation_claim(question, course_reference="CSE 3311"))
        self.assertEqual(result["status"], "indexing_needed")
        self.assertIsNone(result.get("answer_basis"))
        self.assertFalse(provider.general_contexts)


if __name__ == "__main__":
    unittest.main()
