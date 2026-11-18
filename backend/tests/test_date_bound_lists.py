"""EC-023: "what is due tonight?" must answer with the deadlines in that window, not every assignment's title."""
import copy
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from backend.app import canvas_service
from backend.app.retrieval_router import route_query
from backend.app.structured_canvas_service import DATE_PHRASE_HELP
from backend.tests.test_query_routing import NOW, RoutingFixture, assignment  # NOW: Sunday 2026-11-01 12:00 in Chicago

NONE_DUE = "No Canvas assignments have a deadline in that window."


class DateBoundListTests(RoutingFixture, unittest.TestCase):
    def raw(self, **due_by_id):
        """The course's raw Canvas assignments, with chosen due dates, e.g. raw(a106="2026-11-03T05:59:00Z")."""
        records = [copy.deepcopy(a["metadata"]) for a in self.assignments]
        for key, due in due_by_id.items():
            for record in records:
                if record["id"] == int(key[1:]):
                    record["due_at"] = due
        return records

    def ask(self, question, intent, records, now=NOW, **plan_changes):
        plan = self.plan(question, intent, **plan_changes)
        with patch.object(canvas_service, "get_assignments", return_value=records):
            return route_query(42, question, plan, store=self.store, provider=self.embedding, now=now)

    def text(self, result):
        return "\n".join(item["text"] for item in result["results"])

    def test_the_reported_bug_a_list_request_that_names_tonight_is_not_a_list_of_everything(self):
        """The model said intent=list_assignments with deadline_reference='tonight'; nothing is due tonight."""
        result = self.ask("what is due tonight?", "list_assignments", self.raw(), deadline_reference="tonight", requires_live_canvas=True)
        self.assertEqual(result["retrieval_route"], "current_assignment_facts")          # not "current_assignment_list"
        self.assertEqual(self.text(result), NONE_DUE)
        self.assertEqual(result["resolved_assignments"], [])
        self.assertNotIn("Assignment:", self.text(result))                               # not the bare title list
        self.assertEqual(len(self.embedding.calls), 0)

    def test_what_is_due_tonight_lists_only_what_is_due_tonight_with_its_status(self):
        tonight = "2026-11-02T05:59:00Z"                                                   # Sunday 11:59 PM in Chicago
        result = self.ask("what is due tonight?", "list_assignments", self.raw(a106=tonight), deadline_reference="tonight")
        text = self.text(result)
        self.assertEqual(result["resolved_assignments"], [106])
        self.assertIn("HW 6: deadline 2026-11-01 23:59 CST (America/Chicago).", text)
        self.assertIn("HW 6: Canvas submission status is unsubmitted.", text)
        for other in ("HW 1", "HW 10", "Iteration 3"):
            self.assertNotIn(other, text)

    def test_the_same_request_labeled_get_deadline_gives_the_same_assignments(self):
        records = self.raw(a106="2026-11-02T05:59:00Z")
        listed = self.ask("what is due tonight?", "list_assignments", records, deadline_reference="tonight")
        direct = self.ask("what is due tonight?", "get_deadline", records, deadline_reference="tonight", requested_data=["deadline"])
        self.assertEqual(listed["resolved_assignments"], direct["resolved_assignments"])
        self.assertEqual(listed["retrieval_route"], direct["retrieval_route"])

    def test_phrases_that_used_to_be_rejected_now_filter_a_list_request(self):
        records = self.raw(a106="2026-11-03T05:59:00Z", a101="2026-11-07T05:59:00Z")       # HW 6 Mon night, HW 1 Fri night
        self.assertEqual(self.ask("what is due tomorrow", "list_assignments", records, deadline_reference="tomorrow")["resolved_assignments"], [106])
        self.assertEqual(self.ask("what is due by Friday", "list_assignments", records, deadline_reference="by friday")["resolved_assignments"], [106, 110, 201, 202, 203, 101])   # by Friday: everything through Fri 11:59 PM, soonest first
        self.assertEqual(self.ask("what is due before Thursday", "list_assignments", records, deadline_reference="before thursday")["resolved_assignments"], [106])
        wednesday = datetime(2026, 11, 4, 18, 0, tzinfo=timezone.utc)                       # 12:00 Wednesday in Chicago
        saturday_night = self.raw(a101="2026-11-08T05:59:00Z")                              # Saturday 11:59 PM
        self.assertEqual(self.ask("what is due this weekend", "list_assignments", saturday_night, now=wednesday,
                                  deadline_reference="this weekend")["resolved_assignments"], [101])
        self.assertEqual(self.ask("what is due this weekend", "list_assignments", records,                    # on a Sunday: the weekend under way
                                  deadline_reference="this weekend")["resolved_assignments"], [])

    def test_a_deadline_in_the_last_minute_of_the_day_uses_the_course_time_zone(self):
        late = datetime(2026, 11, 2, 5, 30, tzinfo=timezone.utc)                          # 11:30 PM Sunday in Chicago, already Monday in UTC
        records = self.raw(a106="2026-11-02T05:59:00Z", a101="2026-11-02T06:00:00Z")      # 11:59 PM tonight vs midnight tomorrow
        result = self.ask("what is due tonight?", "list_assignments", records, now=late, deadline_reference="tonight")
        self.assertEqual(result["resolved_assignments"], [106])

    def test_an_unrecognized_date_asks_the_student_instead_of_dumping_every_assignment(self):
        result = self.ask("what is due by banana", "list_assignments", self.raw(), deadline_reference="by banana")
        self.assertEqual(result["notice_status"], "clarification_needed")
        self.assertIn(DATE_PHRASE_HELP, result["notice"])
        self.assertEqual(result["results"], [])

    def test_homework_only_requests_stay_homework_only_inside_a_window(self):
        records = self.raw(a106="2026-11-03T05:59:00Z", a201="2026-11-03T05:59:00Z")
        records[2]["name"] = "Homework 6"                                                   # titled like real course work
        result = self.ask("which homework is due tomorrow", "list_assignments", records, deadline_reference="tomorrow", entity_type="homework")
        self.assertEqual(result["resolved_assignments"], [106])

    def test_a_plain_list_without_a_date_is_unchanged(self):
        result = self.ask("list my assignments", "list_assignments", self.raw())
        self.assertEqual(result["retrieval_route"], "current_assignment_list")
        self.assertEqual(len(result["resolved_assignments"]), 6)
        self.assertIn("Assignment: HW 1.", self.text(result))
        self.assertNotIn("deadline", self.text(result))

    def test_a_plain_list_that_asks_about_due_dates_shows_them(self):
        result = self.ask("list my assignments and when they are due", "list_assignments", self.raw())
        self.assertEqual(result["retrieval_route"], "current_assignment_list")
        self.assertEqual(len(result["resolved_assignments"]), 6)
        text = self.text(result)
        self.assertIn("HW 1: deadline 2026-11-05 23:59 CST (America/Chicago).", text)
        self.assertNotIn("Assignment: HW 1.", text)

    def test_a_blank_date_reference_does_not_trigger_the_conversion(self):
        for blank in ("", "   "):
            result = self.ask("list my homework", "list_assignments", self.raw(), deadline_reference=blank)
            self.assertEqual(result["retrieval_route"], "current_assignment_list")

    def test_a_named_assignment_with_its_deadline_is_unaffected(self):
        records = self.raw()
        plan = self.plan("when is HW 6 due", "get_deadline", assignment_reference="HW 6", requested_data=["deadline"])
        with patch.object(canvas_service, "get_assignments", return_value=records), \
             patch.object(canvas_service, "get_assignment", return_value=records[2]):
            result = route_query(42, "when is HW 6 due", plan, store=self.store, provider=self.embedding, now=NOW)
        self.assertEqual(result["resolved_assignments"], [106])
        self.assertNotIn("submission status", self.text(result))                           # still just the deadline, as before


if __name__ == "__main__":
    unittest.main()
