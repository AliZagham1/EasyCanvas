"""EC-013: answers say whether they come from general knowledge or course material.

Fake providers and temporary SQLite only; these do not measure live model
classification (see the paid live-query evaluation for that).
"""

import unittest

from backend.app.query_understanding_service import empty_understanding
from backend.tests import test_general_concept_route as general_route
from backend.tests.test_query_routing import RoutingFixture

STANDALONE = "What are the advantages and disadvantages of FCFS scheduling?"
EXPLICIT = "According to my CSE 3311 slides, what is orthogonal projection?"


class GeneralKnowledgeRoutingTests(RoutingFixture, unittest.TestCase):
    def test_standalone_concept_in_a_selected_course_may_use_general_knowledge(self):
        # Selecting a course does not force every concept question through RAG.
        result = self.route(STANDALONE, self.plan(STANDALONE, "general_concept"))
        self.assertEqual(result["retrieval_route"], "general_concept")
        self.assertFalse(self.embedding.calls)

    def test_explicit_course_material_requests_retrieve_course_material(self):
        # Every plausible model plan shape for "my CSE 3311 slides" must search
        # the course, never answer from general knowledge or claim a missing file.
        plans = {
            "course reference only": self.plan(EXPLICIT, "course_wide_question", course_reference="CSE 3311"),
            "course + generic slides": self.plan(EXPLICIT, "course_wide_question", course_reference="CSE 3311",
                                                 document_reference="CSE 3311 slides"),
            "misclassified general": self.plan(EXPLICIT, "general_concept", course_reference="CSE 3311"),
            "generic slides": self.plan(EXPLICIT, "explain_document", document_reference="slides"),
            "lecture slides": self.plan(EXPLICIT, "find_course_material", document_reference="lecture slides"),
            "my slides": self.plan(EXPLICIT, "explain_question", document_reference="my slides"),
            "course name": self.plan(EXPLICIT, "explain_question", document_reference="Software Engineering lecture notes"),
        }
        for label, plan in plans.items():
            with self.subTest(label):
                result = self.route(EXPLICIT, plan)
                self.assertEqual(result["retrieval_route"], "course_material")
                self.assertNotIn("notice", result)
                self.assertTrue(result["results"])
                self.assertTrue(all(o["course_id"] == 42 for item in result["results"] for o in item["sources"]))

    def test_unmatched_specific_document_reference_still_reports_missing(self):
        for reference in ("Lecture 7 slides", "Kernel Internals slides", "HW 99 notes"):
            with self.subTest(reference):
                question = f"Explain {reference}"
                result = self.route(question, self.plan(question, "explain_document", document_reference=reference))
                self.assertNotEqual(result["retrieval_route"], "course_material")
                self.assertNotEqual(result["retrieval_route"], "general_concept")
                self.assertTrue(result.get("notice"))

    def test_named_file_still_resolves_to_that_file(self):
        question = "Explain Practice Questions 10.pdf"
        result = self.route(question, self.plan(question, "explain_document", document_reference="Practice Questions 10.pdf"))
        self.assertEqual(result["retrieval_route"], "document_material")
        self.assertEqual(result["resolved_documents"], ["file:10"])


class AnswerBasisConversationTests(RoutingFixture, unittest.IsolatedAsyncioTestCase):
    # Borrow helpers by module reference so the borrowed TestCase is not collected twice.
    asyncSetUp = general_route.GeneralConceptStreamTests.asyncSetUp
    turn = general_route.GeneralConceptStreamTests.turn

    async def test_general_answer_is_marked_uncited_and_persisted(self):
        result, _ = await self.turn(STANDALONE, empty_understanding(STANDALONE, intent="general_concept"))
        self.assertEqual((result["retrieval_route"], result["answer_basis"]), ("general_concept", "general_knowledge"))
        self.assertEqual(result["sources"], [])
        saved = self.service.repository.load(self.store.namespace, 42, self.session.id)
        self.assertEqual(saved["messages"][-1]["response"]["answer_basis"], "general_knowledge")

    async def test_course_grounded_answer_has_citations_and_no_general_basis(self):
        result, provider = await self.turn(EXPLICIT, empty_understanding(EXPLICIT, intent="explain_question",
                                                                          document_reference="my slides"))
        self.assertEqual(result["status"], "answered")
        self.assertEqual(result["retrieval_route"], "course_material")
        self.assertTrue(result["sources"])
        self.assertIsNone(result.get("answer_basis"))
        self.assertFalse(provider.general_contexts)

    async def test_followup_about_slides_after_a_general_answer_uses_course_material(self):
        await self.turn(STANDALONE, empty_understanding(STANDALONE, intent="general_concept"))
        question = "What do my lecture slides say about it?"
        result, provider = await self.turn(question, empty_understanding(
            question, intent="explain_question", document_reference="lecture slides", operation="continue"))
        self.assertEqual(result["retrieval_route"], "course_material")
        self.assertIsNone(result.get("answer_basis"))
        self.assertFalse(provider.general_contexts)

    async def test_general_followup_stays_general_and_marked(self):
        await self.turn(STANDALONE, empty_understanding(STANDALONE, intent="general_concept"))
        question = "Give me an example"
        result, _ = await self.turn(question, empty_understanding(question, intent="general_concept",
                                                                   operation="continue", use_active_documents=True))
        self.assertEqual(result["answer_basis"], "general_knowledge")

    async def test_failed_course_retrieval_never_falls_back_to_general_knowledge(self):
        with self.store._connection() as connection:
            connection.execute("DELETE FROM chunk_embeddings")
        for plan in (empty_understanding(EXPLICIT, intent="course_wide_question", course_reference="CSE 3311"),
                     empty_understanding(EXPLICIT, intent="general_concept", course_reference="CSE 3311"),
                     empty_understanding(EXPLICIT, intent="explain_question", document_reference="my slides")):
            with self.subTest(plan["intent"]):
                self.session = None
                result, provider = await self.turn(EXPLICIT, plan)
                self.assertEqual(result["status"], "indexing_needed")
                self.assertIsNone(result.get("answer_basis"))
                self.assertFalse(provider.general_contexts)


class SyncNoticeOnLiveFactsTests(RoutingFixture, unittest.TestCase):
    def test_live_assignment_list_during_discovery_has_no_saved_material_notice(self):
        from unittest.mock import patch
        from backend.app import canvas_service
        self.store.save_course({**self.snapshot, "refresh": {"status": "running", "phase": "discovery", "synced_at": None}})
        records = [a["metadata"] for a in self.assignments]
        with patch.object(canvas_service, "get_assignments", return_value=records) as live:
            result = self.route("what homework do I have", self.plan("homework list", "list_assignments", entity_type="homework"))
        live.assert_called_once()
        self.assertEqual(result["retrieval_route"], "current_assignment_list")
        self.assertNotIn("material_notice", result)


if __name__ == "__main__":
    unittest.main()
