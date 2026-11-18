"""EC-019: an incomplete answer explains what was found, what is missing and what help remains, and never weakens EC-015."""

import copy
import unittest
from unittest.mock import Mock, patch, AsyncMock

from backend.app.query_understanding_service import empty_understanding
from backend.app.rag_service import answer_question, INSUFFICIENT_LEAD
from backend.tests import test_rag as base

RESTRICTED = {"rules": [{"kind": "ai_generated_work_prohibited", "scope": "assignment", "text_hash": "hash-0",
                         "allows_exceptions": False}], "evidence_hashes": ["hash-0"]}


class InsufficientGuidanceTests(unittest.TestCase):
    def setUp(self):
        self.retrieval = base.retrieval_result()
        # The router is replaced so each test chooses the retrieval (and any assignment policy) it exercises.
        patch("backend.app.rag_service.retrieval_router.route_query", side_effect=lambda cid, q, plan, **k: {
            **copy.deepcopy(self.retrieval), "query_understanding": plan, "retrieval_route": "assignment_material"}).start()
        self.addCleanup(patch.stopall)
        store = Mock(); store.load_course.return_value = {"course_id": 42}
        patch("backend.app.rag_service.default_store", return_value=store).start()
        self.intent = "explain_assignment"
        patch("backend.app.rag_service.query_understanding_service.understand_question",
              new=AsyncMock(side_effect=lambda *a, **k: empty_understanding("q", intent=self.intent))).start()
        self.provider = base.FakeLLM({"status": "insufficient_context", "blocks": [], "limitations": [
            "The assignment names a shell but does not list every required menu command."]})

    def ask(self, question="Work on this assignment"):
        return answer_question(42, question, llm_provider=self.provider)

    def test_message_separates_found_missing_and_available_help(self):
        result = self.ask()
        text = result["answer"]
        self.assertEqual(result["status"], "insufficient_context")
        self.assertTrue(text.startswith(INSUFFICIENT_LEAD))
        for heading in ("**What I found**", "**What is missing**", "**What I can still help with**"):
            self.assertIn(heading, text)
        self.assertIn("`Lecture.pptx`", text)
        self.assertIn("does not list every required menu command", text)
        self.assertIn("labeled as general knowledge", text)
        self.assertIn("come only from your course documents", text)
        self.assertEqual((result["sources"], result["answer_blocks"]), ([], []))

    def test_model_blocks_and_code_are_never_shown(self):
        self.provider.answer = {"status": "insufficient_context", "limitations": ["```c\nint main(){}\n```", "Exact commands are not listed."],
                                "blocks": [{"text": "Here is the full solution: int main(){}", "source_ids": ["S1"]}]}
        text = self.ask()["answer"]
        self.assertNotIn("full solution", text)
        self.assertNotIn("int main", text)
        self.assertIn("Exact commands are not listed.", text)

    def test_missing_details_get_a_default_statement(self):
        self.provider.answer = {"status": "insufficient_context", "blocks": [], "limitations": []}
        self.assertIn("do not contain the details this question needs", self.ask()["answer"])

    def test_no_matching_material_does_not_claim_a_document(self):
        self.retrieval = {**self.retrieval, "results": [{**self.retrieval["results"][0], "sources": [
            {**self.retrieval["results"][0]["sources"][0], "title": ""}]}]}
        text = self.ask()["answer"]
        self.assertIn("No passage in your saved material matched", text)
        self.assertNotIn("Explain what the material above says", text)

    def test_document_titles_cannot_break_the_markdown(self):
        self.retrieval["results"][0]["sources"][0]["title"] = "My_`lab`_*3*.pdf"
        text = self.ask()["answer"]
        self.assertIn("`My_'lab'_*3*.pdf`", text)

    def test_restricted_work_omits_approach_help_and_keeps_the_ai_rule_notice(self):
        self.retrieval["assignment_policy"] = copy.deepcopy(RESTRICTED)
        text = self.ask("What does this lab ask?")["answer"]
        self.assertNotIn("Outline a general approach", text)
        self.assertIn("AI-use rules still apply", text)

    def test_unrestricted_work_offers_a_labeled_general_approach(self):
        self.assertIn("Outline a general approach or the steps involved, labeled as general guidance", self.ask()["answer"])

    def test_restricted_solution_requests_are_still_refused_before_generation(self):
        self.retrieval["assignment_policy"] = copy.deepcopy(RESTRICTED)
        self.intent = "solve_question"
        result = self.ask("solve this lab")
        self.assertEqual(self.provider.calls, [])
        self.assertNotIn("What I can still help with", result["answer"])
        self.assertTrue(result["sources"])

    def test_answered_responses_are_unchanged(self):
        self.provider.answer = {"status": "answered", "limitations": [], "blocks": [{"text": "FCFS is simple.", "source_ids": ["S1"]}]}
        result = self.ask()
        self.assertEqual(result["status"], "answered")
        self.assertNotIn("What is missing", result["answer"])


if __name__ == "__main__":
    unittest.main()


import backend.tests.ai_consent_granted  # noqa: E402,F401  (AI tests use fake providers)
