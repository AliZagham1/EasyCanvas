"""EC-020: partial answers with labeled general background, no duplicated limitations, EC-015 unchanged."""

import copy
import unittest
from unittest.mock import Mock, patch, AsyncMock

from backend.app.query_understanding_service import empty_understanding
from backend.app.rag_service import answer_question, RAGValidationError, RAG_INSTRUCTIONS, GENERAL_PREFIX
from backend.tests import test_rag as base
from backend.tests.test_insufficient_guidance import RESTRICTED

CITED = {"text": "The handout asks for a menu-driven shell.", "source_ids": ["S1"]}
GENERAL = {"text": GENERAL_PREFIX + "A shell is usually a read-parse-execute loop.", "source_ids": []}


class PartialAnswerTests(unittest.TestCase):
    def setUp(self):
        self.retrieval = base.retrieval_result()
        patch("backend.app.rag_service.retrieval_router.route_query", side_effect=lambda cid, q, plan, **k: {
            **copy.deepcopy(self.retrieval), "query_understanding": plan, "retrieval_route": "assignment_material"}).start()
        self.addCleanup(patch.stopall)
        store = Mock(); store.load_course.return_value = {"course_id": 42}
        patch("backend.app.rag_service.default_store", return_value=store).start()
        self.intent = "explain_assignment"
        patch("backend.app.rag_service.query_understanding_service.understand_question",
              new=AsyncMock(side_effect=lambda *a, **k: empty_understanding("q", intent=self.intent))).start()
        self.provider = base.FakeLLM()

    def ask(self, answer, question="Help with this assignment"):
        self.provider.answer = answer
        return answer_question(42, question, llm_provider=self.provider)

    def test_partial_answer_keeps_course_citations_and_labeled_general_background(self):
        result = self.ask({"status": "answered", "blocks": [CITED, GENERAL], "limitations": []})
        self.assertEqual(result["status"], "answered")
        self.assertEqual([b["source_ids"] for b in result["answer_blocks"]], [["S1"], []])
        self.assertEqual([s["id"] for s in result["sources"]], ["S1"])
        self.assertTrue(result["answer"].endswith("loop."))
        self.assertIn("[S1]", result["answer"])

    def test_a_bad_uncited_block_is_dropped_without_failing_the_answer(self):
        for text in ("Unlabeled uncited claim.", GENERAL_PREFIX + "See https://example.com",
                     GENERAL_PREFIX + "Pretends to cite [S1]", GENERAL_PREFIX, GENERAL_PREFIX + "   "):
            with self.subTest(block=text):
                result = self.ask({"status": "answered", "blocks": [CITED, {"text": text, "source_ids": []}], "limitations": []})
                self.assertEqual(result["status"], "answered")
                self.assertEqual(result["answer_blocks"], [CITED])
                self.assertNotIn("example.com", result["answer"])
                self.assertNotIn("uncited claim", result["answer"])

    def test_valid_cited_content_survives_next_to_a_dropped_block(self):
        result = self.ask({"status": "answered", "blocks": [CITED, {"text": "Invented rule.", "source_ids": []}, GENERAL], "limitations": []})
        self.assertEqual([b["source_ids"] for b in result["answer_blocks"]], [["S1"], []])

    def test_unknown_citations_still_fail(self):
        with self.assertRaises(RAGValidationError):
            self.ask({"status": "answered", "blocks": [CITED, {"text": "x", "source_ids": ["S99"]}], "limitations": []})

    def test_an_answer_without_any_cited_block_still_fails(self):
        for blocks in ([GENERAL], [{"text": "Unlabeled.", "source_ids": []}]):
            with self.subTest(blocks=blocks), self.assertRaises(RAGValidationError):
                self.ask({"status": "answered", "blocks": blocks, "limitations": []})

    def test_general_background_is_dropped_under_an_ai_restriction(self):
        self.retrieval["assignment_policy"] = copy.deepcopy(RESTRICTED)
        result = self.ask({"status": "answered", "blocks": [CITED, GENERAL], "limitations": []}, "What does this lab ask?")
        self.assertEqual([b["source_ids"] for b in result["answer_blocks"]], [["S1"]])
        self.assertNotIn("read-parse-execute", result["answer"])

    def test_restricted_solution_requests_still_refuse_before_generation(self):
        self.retrieval["assignment_policy"] = copy.deepcopy(RESTRICTED)
        self.intent = "solve_question"
        self.ask({"status": "answered", "blocks": [CITED, GENERAL], "limitations": []}, "solve this lab")
        self.assertEqual(self.provider.calls, [])

    def test_gap_statement_is_shown_once_when_insufficient(self):
        gap = "The handout does not say which commands the menu must offer."
        result = self.ask({"status": "insufficient_context", "blocks": [], "limitations": [gap]})
        self.assertIn(gap, result["answer"])
        self.assertNotIn(gap, result["warnings"])

    def test_limitation_already_stated_in_the_answer_is_not_repeated(self):
        said = "The handout does not specify the output format."
        distinct = "Some images in the handout were not read."
        block = {"text": said + " Ask your instructor.", "source_ids": ["S1"]}
        result = self.ask({"status": "answered", "blocks": [block], "limitations": [said, distinct]})
        self.assertNotIn(said, result["warnings"])
        self.assertIn(distinct, result["warnings"])

    def test_extraction_warnings_survive_deduplication(self):
        self.retrieval["results"][0]["sources"][0]["warnings"] = ["legacy_doc_structure_unverified"]
        self.retrieval["results"][0]["sources"][0]["locations"][0]["warnings"] = ["legacy_doc_structure_unverified"]
        result = self.ask({"status": "answered", "blocks": [CITED], "limitations": []})
        self.assertIn("legacy_doc_structure_unverified", result["warnings"])

    def test_prompt_allows_partial_help_without_a_starter_or_reference_solution(self):
        prompt = " ".join(RAG_INSTRUCTIONS.split())
        for phrase in ("starter project", "reference solution", "never write only", "empty source_ids",
                       GENERAL_PREFIX, "Return insufficient_context only when nothing"):
            self.assertIn(phrase, prompt)
        self.assertNotIn("Do not fill evidence gaps with general knowledge", RAG_INSTRUCTIONS)


if __name__ == "__main__":
    unittest.main()
