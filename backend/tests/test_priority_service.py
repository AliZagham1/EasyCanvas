"""Priorities data: submission status, normalization, partial failures, Canvas requests, and the cache."""

import unittest
from unittest.mock import patch

from backend.app import priority_service as ps

COURSE = {"id": 1, "course_code": "CSE 1", "name": "Intro"}


def raw(id_, group=10, points=10, state="unsubmitted", **extra):
    base = {"id": id_, "name": f"A{id_}", "assignment_group_id": group, "points_possible": points,
            "published": True, "grading_type": "points", "submission_types": ["online_upload"],
            "html_url": f"https://canvas.example/courses/1/assignments/{id_}", "due_at": "2026-11-18T05:00:00Z",
            "submission": {"workflow_state": state, "submitted_at": None, "excused": False, "missing": False}}
    base.update(extra)
    return base


class SubmissionStatusTests(unittest.TestCase):
    def test_states(self):
        self.assertEqual(ps.submission_status(raw(1, state="submitted")), "submitted")
        self.assertEqual(ps.submission_status(raw(1, state="graded")), "graded")
        self.assertEqual(ps.submission_status(raw(1, state="unsubmitted")), "incomplete")

    def test_excused_wins(self):
        a = raw(1, state="graded"); a["submission"]["excused"] = True
        self.assertEqual(ps.submission_status(a), "excused")

    def test_missing_submission_data_is_unknown_not_complete(self):
        a = raw(1); a["submission"] = None
        self.assertEqual(ps.submission_status(a), "unknown")
        del a["submission"]
        self.assertEqual(ps.submission_status(a), "unknown")

    def test_on_paper_and_none_unsubmitted_is_unverified(self):
        for types in (["on_paper"], ["none"], [], ["external_tool"], ["none", "on_paper"]):
            self.assertEqual(ps.submission_status(raw(1, submission_types=types)), "unverified", types)

    def test_mixed_with_online_type_is_incomplete(self):
        self.assertEqual(ps.submission_status(raw(1, submission_types=["on_paper", "online_upload"])), "incomplete")

    def test_graded_but_flagged_missing_stays_incomplete(self):
        a = raw(1, state="graded"); a["submission"]["missing"] = True
        self.assertEqual(ps.submission_status(a), "incomplete")


class NormalizationTests(unittest.TestCase):
    def test_fields_link_and_invalid_dates(self):
        a = raw(7, due_at="not-a-date", unlock_at="2026-11-01T00:00:00Z", group_category_id=3)
        item = ps.build_course_items(COURSE, [a])[0]
        self.assertIsNone(item["due_at"]); self.assertEqual(item["unlock_at"], "2026-11-01T00:00:00Z")
        self.assertTrue(item["is_group"])
        self.assertEqual(item["html_url"], "https://canvas.example/courses/1/assignments/7")
        self.assertEqual(item["course_id"], 1)

    def test_unpublished_assignments_are_skipped(self):
        items = ps.build_course_items(COURSE, [raw(1, published=False), raw(2)])
        self.assertEqual([i["id"] for i in items], [2])


class AggregationTests(unittest.TestCase):
    def setUp(self):
        ps._cache.clear()
        self.addCleanup(ps._cache.clear)
        self.courses = [COURSE, {"id": 2, "course_code": "MATH 2", "name": "Math"}]

    def patches(self, assignments):
        return (patch.object(ps, "_load_courses", return_value=self.courses),
                patch.object(ps, "_fetch_assignments", side_effect=assignments))

    def run_with(self, assignments, **kwargs):
        a, b = self.patches(assignments)
        with a, b as fetch:
            return ps.get_priorities(**kwargs), fetch

    def test_partial_failure_is_reported_not_hidden(self):
        def fetch(course_id):
            if course_id == 2:
                raise ps.canvas_service.requests.ConnectionError("down")
            return [raw(1)]
        result, _ = self.run_with(fetch)
        self.assertEqual(result["failed_course_ids"], [2])
        self.assertEqual([a["course_id"] for a in result["assignments"]], [1])

    def test_only_the_assignment_listing_is_requested_from_canvas(self):
        """No grade-calculation requests (assignment groups, course settings) are made any more."""
        with patch.object(ps, "_load_courses", return_value=[COURSE]), \
             patch.object(ps.canvas_service, "_get_paginated", return_value=[raw(1)]) as listing, \
             patch.object(ps.canvas_service, "_request_get") as single:
            result = ps.get_priorities()
        self.assertEqual(listing.call_count, 1)
        self.assertTrue(listing.call_args[0][0].endswith("/courses/1/assignments"))
        self.assertEqual(listing.call_args[0][1], {"include[]": "submission"})
        single.assert_not_called()
        self.assertEqual(result["failed_course_ids"], [])
        self.assertNotIn("grade_weight", result["assignments"][0])

    def test_cache_hit_then_refresh_refetches(self):
        result, fetch = self.run_with(lambda cid: [raw(cid)])
        self.assertFalse(result["cached"]); self.assertEqual(fetch.call_count, 2)
        a, b = self.patches(lambda cid: [raw(cid)])
        with a as courses, b as fetch2:
            cached = ps.get_priorities()
            self.assertTrue(cached["cached"]); self.assertEqual(fetch2.call_count, 0)
            fresh = ps.get_priorities(refresh=True)
            self.assertFalse(fresh["cached"]); self.assertEqual(fetch2.call_count, 2)

    def test_failed_refresh_does_not_resurrect_older_data(self):
        self.run_with(lambda cid: [raw(cid)])
        def fetch(course_id):
            raise ps.canvas_service.requests.Timeout("slow")
        a, b = self.patches(fetch)
        with a, b:
            self.assertEqual(sorted(ps.get_priorities(refresh=True)["failed_course_ids"]), [1, 2])
            self.assertEqual(sorted(ps.get_priorities()["failed_course_ids"]), [1, 2])

    def test_failures_are_never_cached(self):
        calls = []
        def fetch(course_id):
            calls.append(course_id)
            if len(calls) <= 2:
                raise ps.canvas_service.requests.Timeout("x")
            return [raw(course_id)]
        a, b = self.patches(fetch)
        with a, b:
            self.assertEqual(len(ps.get_priorities()["failed_course_ids"]), 2)
            self.assertEqual(ps.get_priorities()["failed_course_ids"], [])


class CacheOrderingTests(unittest.TestCase):
    def test_older_fetch_cannot_overwrite_newer(self):
        cache = ps.PriorityCache()
        self.assertTrue(cache.put("k", 2.0, "t2", "new"))
        self.assertFalse(cache.put("k", 1.0, "t1", "old"))
        self.assertEqual(cache.get("k")["value"], "new")

    def test_ttl_expiry(self):
        now = [0.0]
        cache = ps.PriorityCache(ttl=60, clock=lambda: now[0])
        cache.put("k", 0.0, "t", "v")
        now[0] = 59; self.assertIsNotNone(cache.get("k"))
        now[0] = 61; self.assertIsNone(cache.get("k"))


if __name__ == "__main__":
    unittest.main()


import backend.tests.ai_consent_granted  # noqa: E402,F401  (AI tests use fake providers)
