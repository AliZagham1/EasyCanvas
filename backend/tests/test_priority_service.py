"""Priorities data: submission status, guarded grade-weight estimates, partial failures, and the cache."""

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


def weights(*pairs):
    return [{"id": gid, "group_weight": w, "rules": {}} for gid, w in pairs]


WEIGHTED = {"apply_assignment_group_weights": True, "hide_final_grades": False}
POINTS = {"apply_assignment_group_weights": False, "hide_final_grades": False}


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


class GradeWeightTests(unittest.TestCase):
    def test_weighted_group_share(self):
        items = [raw(1, group=10, points=5), raw(2, group=10, points=15), raw(3, group=20, points=100)]
        groups = weights((10, 40), (20, 60))
        self.assertEqual(ps.estimate_grade_weight(items[0], items, groups, WEIGHTED)["percent"], 10.0)
        self.assertEqual(ps.estimate_grade_weight(items[1], items, groups, WEIGHTED)["percent"], 30.0)

    def test_points_course_share_of_all_counting_points(self):
        items = [raw(1, points=25), raw(2, points=75)]
        self.assertEqual(ps.estimate_grade_weight(items[0], items, weights((10, 0)), POINTS)["percent"], 25.0)

    def test_points_possible_is_not_percentage(self):
        items = [raw(1, points=100), raw(2, points=100)]
        self.assertEqual(ps.estimate_grade_weight(items[0], items, weights((10, 0)), POINTS)["percent"], 50.0)

    def test_weights_not_summing_to_100_unavailable(self):
        items = [raw(1, group=10), raw(2, group=20)]
        result = ps.estimate_grade_weight(items[0], items, weights((10, 52), (20, 51)), WEIGHTED)
        self.assertIsNone(result["percent"]); self.assertTrue(result["reason"])

    def test_empty_weighted_group_unavailable(self):
        items = [raw(1, group=10)]
        result = ps.estimate_grade_weight(items[0], items, weights((10, 80), (20, 20)), WEIGHTED)
        self.assertIsNone(result["percent"])

    def test_drop_rules_unavailable(self):
        items = [raw(1)]
        groups = [{"id": 10, "group_weight": 100, "rules": {"drop_lowest": 1}}]
        self.assertIsNone(ps.estimate_grade_weight(items[0], items, groups, WEIGHTED)["percent"])

    def test_extra_credit_zero_point_unavailable(self):
        items = [raw(1, points=10), raw(2, points=0)]
        self.assertIsNone(ps.estimate_grade_weight(items[0], items, weights((10, 100)), WEIGHTED)["percent"])
        self.assertIsNone(ps.estimate_grade_weight(items[1], items, weights((10, 100)), WEIGHTED)["percent"])

    def test_hidden_grades_omitted_and_ungraded_unavailable(self):
        items = [raw(1)]
        g = weights((10, 100))
        self.assertIsNone(ps.estimate_grade_weight(items[0], items, g, {**WEIGHTED, "hide_final_grades": True})["percent"])
        omitted = [raw(1, omit_from_final_grade=True)]
        self.assertIsNone(ps.estimate_grade_weight(omitted[0], omitted, g, WEIGHTED)["percent"])
        ungraded = [raw(1, grading_type="not_graded")]
        self.assertIsNone(ps.estimate_grade_weight(ungraded[0], ungraded, g, WEIGHTED)["percent"])

    def test_missing_grading_data_is_unavailable_never_zero(self):
        items = [raw(1)]
        self.assertIsNone(ps.estimate_grade_weight(items[0], items, None, None)["percent"])
        self.assertIsNone(ps.estimate_grade_weight(items[0], items, weights((10, 100)), None)["percent"])

    def test_excused_work_leaves_the_denominator(self):
        excused = raw(2, points=10); excused["submission"]["excused"] = True
        items = [raw(1, points=10), excused]
        self.assertEqual(ps.estimate_grade_weight(items[0], items, weights((10, 100)), WEIGHTED)["percent"], 100.0)


class NormalizationTests(unittest.TestCase):
    def test_fields_link_and_invalid_dates(self):
        a = raw(7, due_at="not-a-date", unlock_at="2026-11-01T00:00:00Z", group_category_id=3)
        item = ps.build_course_items(COURSE, [a], weights((10, 100)), WEIGHTED)[0]
        self.assertIsNone(item["due_at"]); self.assertEqual(item["unlock_at"], "2026-11-01T00:00:00Z")
        self.assertTrue(item["is_group"])
        self.assertEqual(item["html_url"], "https://canvas.example/courses/1/assignments/7")
        self.assertEqual(item["course_id"], 1)

    def test_unpublished_assignments_are_skipped(self):
        items = ps.build_course_items(COURSE, [raw(1, published=False), raw(2)], weights((10, 100)), WEIGHTED)
        self.assertEqual([i["id"] for i in items], [2])


class AggregationTests(unittest.TestCase):
    def setUp(self):
        ps._cache.clear()
        self.addCleanup(ps._cache.clear)
        self.courses = [COURSE, {"id": 2, "course_code": "MATH 2", "name": "Math"}]

    def patches(self, assignments):
        return (patch.object(ps, "_load_courses", return_value=self.courses),
                patch.object(ps, "_fetch_assignments", side_effect=assignments),
                patch.object(ps, "_fetch_groups", return_value=weights((10, 100))),
                patch.object(ps, "_fetch_settings", return_value=WEIGHTED))

    def run_with(self, assignments, **kwargs):
        a, b, c, d = self.patches(assignments)
        with a, b as fetch, c, d:
            return ps.get_priorities(**kwargs), fetch

    def test_partial_failure_is_reported_not_hidden(self):
        def fetch(course_id):
            if course_id == 2:
                raise ps.canvas_service.requests.ConnectionError("down")
            return [raw(1)]
        result, _ = self.run_with(fetch)
        self.assertEqual(result["failed_course_ids"], [2])
        self.assertEqual([a["course_id"] for a in result["assignments"]], [1])

    def test_missing_grading_data_does_not_fail_the_course(self):
        with patch.object(ps, "_load_courses", return_value=[COURSE]), \
             patch.object(ps, "_fetch_assignments", return_value=[raw(1)]), \
             patch.object(ps, "_fetch_groups", side_effect=RuntimeError("403")):
            result = ps.get_priorities()
        self.assertEqual(result["failed_course_ids"], [])
        self.assertIsNone(result["assignments"][0]["grade_weight"]["percent"])

    def test_cache_hit_then_refresh_refetches(self):
        result, fetch = self.run_with(lambda cid: [raw(cid)])
        self.assertFalse(result["cached"]); self.assertEqual(fetch.call_count, 2)
        a, b, c, d = self.patches(lambda cid: [raw(cid)])
        with a as courses, b as fetch2, c, d:
            cached = ps.get_priorities()
            self.assertTrue(cached["cached"]); self.assertEqual(fetch2.call_count, 0)
            fresh = ps.get_priorities(refresh=True)
            self.assertFalse(fresh["cached"]); self.assertEqual(fetch2.call_count, 2)

    def test_failed_refresh_does_not_resurrect_older_data(self):
        self.run_with(lambda cid: [raw(cid)])
        def fetch(course_id):
            raise ps.canvas_service.requests.Timeout("slow")
        a, b, c, d = self.patches(fetch)
        with a, b, c, d:
            self.assertEqual(sorted(ps.get_priorities(refresh=True)["failed_course_ids"]), [1, 2])
            self.assertEqual(sorted(ps.get_priorities()["failed_course_ids"]), [1, 2])

    def test_failures_are_never_cached(self):
        calls = []
        def fetch(course_id):
            calls.append(course_id)
            if len(calls) <= 2:
                raise ps.canvas_service.requests.Timeout("x")
            return [raw(course_id)]
        a, b, c, d = self.patches(fetch)
        with a, b, c, d:
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
