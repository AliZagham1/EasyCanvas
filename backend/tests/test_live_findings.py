"""EC-024: problems found by running real demo questions (stray citation markup, "by Friday", "what's due?", scope, office hours)."""
import copy
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from backend.app import canvas_service, rag_service, retrieval_router
from backend.app.retrieval_router import SINGLE_COURSE_NOTICE, _course_policy_request, route_query
from backend.app.structured_canvas_service import date_window, restore_date_qualifier, UPCOMING_DAYS
from backend.tests.test_query_routing import NOW, RoutingFixture
from backend.app.chunking_service import ensure_course_chunks
from backend.app.embedding_service import embed_course


def validated(blocks, limitations=(), ids=("S1", "S2", "S3")):
    answer = {"status": "answered", "blocks": copy.deepcopy(blocks), "limitations": list(limitations)}
    rag_service._validate_answer(answer, set(ids))
    return answer


class ModelCitationMarkupTests(unittest.TestCase):
    """Live bug: "...full credit. \\ue200cite\\ue202S1\\ue201 [S1]" showed as a stray "citeS1"."""
    def test_hidden_model_citation_markup_is_removed(self):
        raw = "Submit by the posted deadline to receive full credit. citeS1 [S1]"
        answer = validated([{"text": raw, "source_ids": ["S1"]}])
        self.assertEqual(answer["blocks"][0]["text"], "Submit by the posted deadline to receive full credit.")
        self.assertNotIn("cite", answer["blocks"][0]["text"])

    def test_variants_and_stray_private_use_characters_are_removed(self):
        for raw in ("A fact.citeS1", "A fact. CITES1S2 [S1]", "A fact."):
            text = validated([{"text": raw, "source_ids": ["S1"]}])["blocks"][0]["text"]
            self.assertEqual(text, "A fact.", repr(raw))

    def test_general_knowledge_blocks_and_limitations_are_cleaned_too(self):
        label = rag_service.GENERAL_PREFIX
        answer = validated([{"text": "Real course claim [S1]", "source_ids": ["S1"]},
                            {"text": label + "A deadlock needs four conditions.citeS9", "source_ids": []}],
                           limitations=["Some content could not be read.citeS1"])
        self.assertNotIn("", str(answer))
        self.assertTrue(answer["blocks"][1]["text"].endswith("four conditions."))
        self.assertEqual(answer["limitations"], ["Some content could not be read."])

    def test_general_concept_answers_are_cleaned(self):
        response = {"answer": {"status": "answered", "limitations": [], "blocks": [{"text": "A deadlock is a stall.citeS1", "source_ids": []}]},
                    "model": "m", "usage": None}
        result = rag_service._general_answer(42, {"query_understanding": {}}, response)
        self.assertEqual(result["answer"], "A deadlock is a stall.")

    def test_a_duplicate_parenthesized_citation_is_removed_but_real_text_is_kept(self):
        text = validated([{"text": "You may not use AI for graded work. (S3) [S3]\n\nAlso see (S1, S2) and x (S9) and foo(S1).", "source_ids": ["S1", "S2", "S3"]}])["blocks"][0]["text"]
        self.assertNotIn("(S3)", text)
        self.assertNotIn("(S1, S2)", text)
        self.assertIn("(S9)", text)                                                      # not a declared citation: left alone
        self.assertIn("foo(S1)", text)                                                   # attached to code: left alone

    def test_ordinary_text_is_unchanged(self):
        text = "Late work loses **25%** within 24 hours (see policy). Use `f(x)` here."
        self.assertEqual(validated([{"text": text, "source_ids": ["S1"]}])["blocks"][0]["text"], text)


class QualifierTests(unittest.TestCase):
    def test_the_qualifier_the_model_dropped_is_restored_from_the_students_words(self):
        cases = [("Friday", "what's due by Friday?", "by Friday"), ("Friday", "what is due before friday", "before Friday"),
                 ("next week", "anything due through next week", "through next week"), ("the 20th", "what's due until the 20th?", "until the 20th"),
                 ("Friday", "what's due no later than Friday", "no later than Friday"), ("Friday", "what is due BY   friday", "by Friday")]
        for reference, question, expected in cases:
            self.assertEqual(restore_date_qualifier(reference, question), expected, question)

    def test_nothing_changes_when_there_is_nothing_to_restore(self):
        for reference, question in (("Friday", "what is due Friday?"), ("by Friday", "what's due by Friday?"), ("tomorrow", "what's due by Friday?"),
                                    ("", "what's due by Friday?"), ("Friday", "")):
            self.assertEqual(restore_date_qualifier(reference, question), reference)
        for odd in (None, 5, ["friday"]):
            self.assertEqual(restore_date_qualifier(odd, "by friday"), odd)

    def test_the_restored_phrase_gives_the_by_window(self):
        now = datetime(2026, 11, 18, 17, 15, tzinfo=timezone.utc)                         # Wednesday 11:15 AM in Chicago
        start, end = date_window(restore_date_qualifier("Friday", "what's due by Friday?"), now, "America/Chicago")
        self.assertEqual((start.strftime("%a %d"), end.strftime("%a %d")), ("Wed 18", "Sat 21"))


class RoutedLiveFindingTests(RoutingFixture, unittest.TestCase):
    WED = datetime(2026, 11, 4, 18, 0, tzinfo=timezone.utc)                              # 12:00 Wednesday in Chicago

    def raw(self, **due_by_id):
        records = [copy.deepcopy(a["metadata"]) for a in self.assignments]
        for key, due in due_by_id.items():
            for record in records:
                if record["id"] == int(key[1:]):
                    record["due_at"] = due
        return records

    def ask(self, question, intent, records, now=None, **changes):
        plan = self.plan(question, intent, **changes)
        with patch.object(canvas_service, "get_assignments", return_value=records):
            return route_query(42, question, plan, store=self.store, provider=self.embedding, now=now or self.WED)

    def test_by_friday_includes_thursday_even_when_the_model_wrote_just_friday(self):
        """Live: model said deadline_reference='Friday' for "what's due by Friday?" and the answer was empty."""
        records = self.raw(a106="2026-11-06T05:59:00Z")                                    # Thursday 11:59 PM
        result = self.ask("what's due by Friday?", "get_deadline", records, deadline_reference="Friday", requested_data=["deadline"])
        self.assertIn(106, result["resolved_assignments"])
        plain = self.ask("what's due Friday?", "get_deadline", records, deadline_reference="Friday", requested_data=["deadline"])
        self.assertNotIn(106, plain["resolved_assignments"])                                # without "by", Friday means only Friday

    def test_whats_due_with_no_date_means_the_next_two_weeks_not_everything_since_september(self):
        records = self.raw(a101="2026-09-03T05:59:00Z", a110="2026-12-30T05:59:00Z", a106="2026-11-06T05:59:00Z", a201="2026-11-17T05:59:00Z")
        result = self.ask("whats due?", "list_assignments", records)
        ids = result["resolved_assignments"]
        self.assertIn(106, ids)
        self.assertIn(201, ids)
        self.assertNotIn(101, ids)                                                         # a September deadline is not "due"
        self.assertNotIn(110, ids)                                                         # nor one months away
        self.assertEqual(result["retrieval_route"], "current_assignment_facts")

    def test_the_empty_upcoming_answer_says_what_window_was_checked(self):
        far = {f"a{a['canvas_id']}": "2026-12-30T05:59:00Z" for a in self.assignments}
        result = self.ask("what is due?", "list_assignments", self.raw(**far))
        self.assertEqual(result["results"][0]["text"], f"No Canvas assignments are due in the next {UPCOMING_DAYS} days.")

    def test_a_request_for_everything_still_lists_everything(self):
        for question in ("list all my assignments that are due", "show every assignment due", "list the assignments and when each is due"):
            result = self.ask(question, "list_assignments", self.raw())
            self.assertEqual(result["retrieval_route"], "current_assignment_list", question)
            self.assertEqual(len(result["resolved_assignments"]), 6, question)

    def test_a_filtered_follow_up_list_is_not_turned_into_upcoming(self):
        result = self.ask("what is due except HW 1", "list_assignments", self.raw(), excluded_references=["HW 1"], operation="exclude")
        self.assertEqual(result["retrieval_route"], "current_assignment_list")

    def test_a_question_about_all_courses_explains_the_one_course_limit(self):
        for reference, question in (("all my courses", "What is due this week in all my courses?"), ("", "what is due across all my courses"),
                                    ("every course", "any deadlines in every course"), ("", "show my courses deadlines")):
            for intent in ("get_deadline", "list_assignments"):
                result = self.ask(question, intent, self.raw(), course_reference=reference, deadline_reference="this week")
                self.assertEqual(result["notice"], SINGLE_COURSE_NOTICE, (question, intent))
                self.assertEqual(result["results"], [])                                    # not a silently narrowed answer

    def test_ordinary_subject_questions_are_not_mistaken_for_cross_course_requests(self):
        """The cross-course guard only applies to deadline and list requests, so a subject question may say "my courses"."""
        for question in ("how do I register my courses in the Java class", "explain all courses of action in a game tree"):
            result = self.ask(question, "course_wide_question", self.raw())
            self.assertNotEqual(result.get("notice"), SINGLE_COURSE_NOTICE, question)
        result = self.ask("when is the Courses assignment due", "get_deadline", self.raw(), deadline_reference="this week")
        self.assertNotEqual(result.get("notice"), SINGLE_COURSE_NOTICE)                 # "Courses" alone is not a cross-course request

    def test_naming_a_different_single_course_keeps_the_original_message(self):
        result = self.ask("what is due in CSE 9999 this week", "get_deadline", self.raw(), course_reference="CSE 9999", deadline_reference="this week")
        self.assertNotEqual(result["notice"], SINGLE_COURSE_NOTICE)
        self.assertIn("Please select the course", result["notice"])


class SyllabusLogisticsRoutingTests(unittest.TestCase):
    def test_logistics_questions_are_answered_from_the_syllabus(self):
        for question in ("What are the instructor's office hours?", "when are office hours", "what is the professor's email",
                         "Who is the instructor?", "how do I contact the professor", "what is the grading scale", "what is the grade breakdown",
                         "where does the class meet", "what are the class meeting times", "according to the syllabus what is required",
                         "what is the late work policy", "what is the attendance policy"):
            self.assertTrue(_course_policy_request(question, "course_wide_question"), question)

    def test_subject_questions_that_share_a_word_stay_on_course_material(self):
        for question in ("Explain contact forces", "What does the instructor module do in the code", "Explain chapter 3 of the textbook",
                         "how do processes communicate", "explain the grade function in the lab", "what is a class in OOP",
                         "how is homework 3 graded", "when is the lab due", "what is the point of a scheduler"):
            self.assertFalse(_course_policy_request(question, "course_wide_question"), question)

    def test_fact_and_list_intents_are_never_captured(self):
        for intent in ("get_deadline", "list_assignments", "solve_question", "study_plan"):
            self.assertFalse(_course_policy_request("what are the office hours", intent), intent)


class OfficeHoursRetrievalTests(RoutingFixture, unittest.TestCase):
    """Live: the real hours ranked 7th behind announcements about CANCELED office hours, so the answer never saw them."""
    def setUp(self):
        super().setUp()
        self.store.save_document(42, {"document_key": "syllabus_file:abc", "canvas_id": 9, "course_id": 42, "source_type": "syllabus_file",
                                      "title": "Syllabus.docx", "status": "ok", "version": "1", "source_role": "course_syllabus",
                                      "text": "Instructor Information\n\nOffice Hours: T/TH 10:30 AM - 12:30 PM in ERB 311"}, self.snapshot["synced_at"])
        for number in range(4):
            self.store.save_document(42, {"document_key": f"announcement:{number}", "canvas_id": 100 + number, "course_id": 42,
                                          "source_type": "announcement", "title": f"Office Hours Canceled {number}", "status": "ok", "version": "1",
                                          "text": "Please note that my office hours this week are canceled."}, self.snapshot["synced_at"])
        ensure_course_chunks(42, self.store)
        embed_course(42, store=self.store, provider=self.embedding)

    def test_the_office_hours_question_is_routed_to_the_syllabus_only(self):
        plan = self.plan("What are the instructor's office hours?", "course_wide_question")
        result = route_query(42, "What are the instructor's office hours?", plan, store=self.store, provider=self.embedding, now=NOW)
        self.assertEqual(result["retrieval_route"], "course_policy_material")
        keys = {origin["document_key"] for item in result["results"] for origin in item["sources"]}
        self.assertEqual(keys, {"syllabus_file:abc"})                                      # the canceled-hours announcements cannot outrank it
        self.assertTrue(any("T/TH 10:30" in item["text"] for item in result["results"]))


if __name__ == "__main__":
    unittest.main()
