import os
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from backend.app import canvas_service


NOW = datetime(2030, 6, 15, tzinfo=timezone.utc)


class FixedDateTime(datetime):
    @classmethod
    def now(cls, tz=None):
        return NOW


def course(course_id, term_id, start="2030-05-01T00:00:00Z", end="2030-08-01T00:00:00Z"):
    return {
        "id": course_id, "name": "Test course", "course_code": "TEST 100",
        "enrollment_term_id": term_id,
        "term": {"id": term_id, "name": "Test term", "start_at": start, "end_at": end},
    }


class CurrentCourseSelectionTests(unittest.TestCase):
    def setUp(self):
        environment = patch.dict(os.environ, {"CANVAS_TERM_ID": ""})
        environment.start()
        self.addCleanup(environment.stop)
        clock = patch.object(canvas_service, "datetime", FixedDateTime)
        clock.start()
        self.addCleanup(clock.stop)

    def test_dates_select_current_term_even_if_past_or_future_ids_are_larger(self):
        records = [course(1, 5), course(2, 900, "2029-01-01T00:00:00Z", "2029-12-01T00:00:00Z"),
                   course(3, 1000, "2031-01-01T00:00:00Z", "2031-12-01T00:00:00Z")]
        with patch.object(canvas_service, "_get_student_courses", return_value=records):
            self.assertEqual([item["id"] for item in canvas_service.get_current_courses()], [1])

    def test_canvas_is_asked_for_active_published_student_courses_and_term_metadata(self):
        with patch.object(canvas_service, "_get_paginated", return_value=[]) as fetch:
            self.assertEqual(canvas_service.get_current_courses(), [])
        self.assertEqual(fetch.call_args.args[1], {
            "per_page": 100, "enrollment_type": "student", "enrollment_state": "active",
            "state[]": "available", "include[]": "term",
        })

    def test_overlapping_current_terms_are_both_kept(self):
        with patch.object(canvas_service, "_get_student_courses", return_value=[course(1, 5), course(2, 8)]):
            self.assertEqual(len(canvas_service.get_current_courses()), 2)

    def test_between_dated_terms_is_a_real_empty_result(self):
        with patch.object(canvas_service, "_get_student_courses", return_value=[course(1, 5, "2029-01-01T00:00:00Z", "2029-12-01T00:00:00Z")]):
            self.assertEqual(canvas_service.get_current_courses(), [])

    def test_missing_invalid_naive_or_reversed_dates_require_explicit_selection(self):
        for start, end in [(None, None), ("invalid", "invalid"),
                           ("2030-05-01T00:00:00", "2030-08-01T00:00:00"),
                           ("2030-08-01T00:00:00Z", "2030-05-01T00:00:00Z")]:
            with self.subTest(start=start, end=end), patch.object(canvas_service, "_get_student_courses", return_value=[course(1, 5, start, end)]):
                with self.assertRaisesRegex(ValueError, "CANVAS_TERM_ID"):
                    canvas_service.get_current_courses()

    def test_undated_extra_course_does_not_replace_a_known_current_term(self):
        with patch.object(canvas_service, "_get_student_courses", return_value=[course(1, 5), course(2, 900, None, None)]):
            self.assertEqual([item["id"] for item in canvas_service.get_current_courses()], [1])

    def test_term_boundary_is_start_inclusive_end_exclusive(self):
        self.assertTrue(canvas_service._term_is_current({"start_at": NOW.isoformat(), "end_at": "2030-08-01T00:00:00Z"}, NOW))
        self.assertFalse(canvas_service._term_is_current({"start_at": "2030-05-01T00:00:00Z", "end_at": NOW.isoformat()}, NOW))

    def test_explicit_term_selects_only_that_accessible_term_without_ranking_ids(self):
        with patch.dict(os.environ, {"CANVAS_TERM_ID": " 005 "}), patch.object(canvas_service, "_get_student_courses", return_value=[course(1, 5, None, None), course(2, 900)]):
            self.assertEqual([item["id"] for item in canvas_service.get_current_courses()], [1])

    def test_invalid_override_is_rejected_before_requesting_canvas(self):
        for value in ["0", "-1", "abc", "2.5", "٥"]:
            with self.subTest(value=value), patch.dict(os.environ, {"CANVAS_TERM_ID": value}), patch.object(canvas_service, "_get_student_courses") as fetch:
                with self.assertRaises(ValueError):
                    canvas_service.get_current_courses()
                fetch.assert_not_called()

    def test_unmatched_override_is_reported_instead_of_silently_hiding_courses(self):
        with patch.dict(os.environ, {"CANVAS_TERM_ID": "99"}), patch.object(canvas_service, "_get_student_courses", return_value=[course(1, 5)]):
            with self.assertRaisesRegex(ValueError, "does not match"):
                canvas_service.get_current_courses()

    def test_term_listing_is_deduplicated_and_keeps_canvas_dates(self):
        with patch.object(canvas_service, "_get_student_courses", return_value=[course(1, 5), course(2, 5), course(3, 8)]):
            terms = canvas_service.get_course_terms()
        self.assertEqual([term["id"] for term in terms], [5, 8])
        self.assertEqual(terms[0]["start_at"], "2030-05-01T00:00:00Z")

    def test_canvas_errors_are_not_replaced_with_an_empty_list(self):
        error = canvas_service.requests.ConnectionError("test")
        with patch.object(canvas_service, "_get_student_courses", side_effect=error):
            with self.assertRaises(canvas_service.requests.ConnectionError):
                canvas_service.get_current_courses()
