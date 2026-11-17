"""Canvas facts for the Priorities page. Ranking itself lives in frontend/lib/priorities.ts.

This module only normalizes read-only Canvas data (assignments, submissions, assignment groups,
course grading settings) and computes an *estimated grade weight* when Canvas data supports it.
It never writes to Canvas, uses no LLM, and never turns unknown data into zero.
"""

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from backend.app import canvas_service

CACHE_TTL_SECONDS = 60
MAX_PARALLEL_COURSES = 4
# Canvas does not reliably record completion for these, so "unsubmitted" is not evidence of anything.
UNTRACKED_SUBMISSION_TYPES = {"", "none", "on_paper", "not_graded", "external_tool"}
WEIGHT_SUM_TOLERANCE = 0.01


# ---------------------------------------------------------------- normalization
def _valid_instant(value):
    """Return the Canvas ISO timestamp if it parses, otherwise None (never invent a date)."""
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return value if parsed.tzinfo else None


def _number(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def submission_status(raw):
    """Classify completion from Canvas submission data; unverifiable work is never 'completed'."""
    submission = raw.get("submission")
    if not isinstance(submission, dict) or not submission.get("workflow_state"):
        return "unknown"
    if submission.get("excused") is True:
        return "excused"
    state = submission["workflow_state"]
    if state in ("submitted", "pending_review"):
        return "submitted"
    if state == "graded":
        # A posted grade is reliable evidence unless Canvas also flags the work as missing.
        return "incomplete" if submission.get("missing") is True and not submission.get("submitted_at") else "graded"
    if state == "unsubmitted":
        types = set(raw.get("submission_types") or [])
        if not types or types <= UNTRACKED_SUBMISSION_TYPES:
            return "unverified"
        return "incomplete"
    return "unknown"


def _counts_toward_grade(raw):
    """Published, graded, points-bearing assignments that Canvas includes in the final grade."""
    return (raw.get("published") is not False and not raw.get("omit_from_final_grade")
            and raw.get("grading_type") != "not_graded" and _number(raw.get("points_possible")) is not None
            and submission_status(raw) != "excused")


def estimate_grade_weight(raw, raw_assignments, groups, settings):
    """Estimated share of the final grade, or percent=None with the reason it is unavailable."""
    def unavailable(reason):
        return {"percent": None, "reason": reason}

    if not isinstance(settings, dict) or groups is None:
        return unavailable("Course grading settings were not available.")
    if settings.get("hide_final_grades"):
        return unavailable("This course hides final grades.")
    if not _counts_toward_grade(raw):
        return unavailable("This assignment does not count toward the final grade.")
    points = raw["points_possible"]
    if points <= 0:
        return unavailable("Zero-point work may be extra credit.")
    if any(group.get("rules") for group in groups):
        return unavailable("Dropped-score rules make the weight unreliable.")
    weighted = settings.get("apply_assignment_group_weights") is True
    counted = [item for item in raw_assignments if _counts_toward_grade(item)]
    if weighted:
        by_id = {group.get("id"): group for group in groups}
        weights = [_number(group.get("group_weight")) for group in groups]
        if any(weight is None for weight in weights) or abs(sum(weights) - 100) > WEIGHT_SUM_TOLERANCE:
            return unavailable("Category weights do not add up to 100%.")
        group = by_id.get(raw.get("assignment_group_id"))
        if group is None:
            return unavailable("The assignment's category was not found.")
        for candidate in groups:
            members = [item for item in counted if item.get("assignment_group_id") == candidate.get("id")]
            if candidate["group_weight"] > 0 and not any(item["points_possible"] > 0 for item in members):
                return unavailable("A weighted category has no graded work, so Canvas may reassign its weight.")
        scope = [item for item in counted if item.get("assignment_group_id") == group.get("id")]
        weight = group["group_weight"]
    else:
        scope, weight = counted, 100
    if any(item["points_possible"] <= 0 for item in scope):
        return unavailable("Zero-point work in this scope may be extra credit.")
    total = sum(item["points_possible"] for item in scope)
    if total <= 0:
        return unavailable("Category points are unavailable.")
    return {"percent": round(weight * points / total, 1), "reason": None}


def normalize_assignment(course, raw, raw_assignments, groups, settings):
    submission = raw.get("submission") if isinstance(raw.get("submission"), dict) else {}
    lock_info = raw.get("lock_info") if isinstance(raw.get("lock_info"), dict) else {}
    return {
        "id": raw.get("id"),
        "course_id": course["id"],
        "course_code": course.get("course_code"),
        "course_name": course.get("name"),
        "name": raw.get("name"),
        "html_url": raw.get("html_url") if isinstance(raw.get("html_url"), str) else None,
        "due_at": _valid_instant(raw.get("due_at")),
        "lock_at": _valid_instant(raw.get("lock_at")),
        "unlock_at": _valid_instant(raw.get("unlock_at")),
        "locked_for_user": raw.get("locked_for_user") is True,
        "lock_unlock_at": _valid_instant(lock_info.get("unlock_at")),
        "points_possible": _number(raw.get("points_possible")),
        "submission_types": list(raw.get("submission_types") or []),
        "is_group": raw.get("group_category_id") is not None,
        "submission_status": submission_status(raw),
        "submitted_at": submission.get("submitted_at"),
        "missing": submission.get("missing") is True,
        "late": submission.get("late") is True,
        "grade_weight": estimate_grade_weight(raw, raw_assignments, groups, settings),
    }


def build_course_items(course, raw_assignments, groups, settings):
    return [normalize_assignment(course, raw, raw_assignments, groups, settings)
            for raw in raw_assignments if raw.get("published") is not False and raw.get("id") is not None]


# ---------------------------------------------------------------- Canvas access (read-only)
def _fetch_assignments(course_id):
    url = f"{canvas_service.CANVAS_URL}/api/v1/courses/{course_id}/assignments"
    return canvas_service._get_paginated(url, {"include[]": ["submission", "all_dates"]})


def _fetch_groups(course_id):
    return canvas_service._get_paginated(f"{canvas_service.CANVAS_URL}/api/v1/courses/{course_id}/assignment_groups")


def _fetch_settings(course_id):
    response = canvas_service._request_get(f"{canvas_service.CANVAS_URL}/api/v1/courses/{course_id}",
                                           headers=canvas_service.headers)
    try:
        return response.json()
    finally:
        response.close()


def _load_courses():
    return canvas_service.get_current_courses()


def _fetch_course(course):
    """Assignments are required; missing grading data only makes the weight unavailable."""
    assignments = _fetch_assignments(course["id"])
    try:
        groups, settings = _fetch_groups(course["id"]), _fetch_settings(course["id"])
    except Exception:  # noqa: BLE001 - grading data is optional; assignments must still be shown.
        groups, settings = None, None
    return build_course_items(course, assignments, groups, settings)


# ---------------------------------------------------------------- cache
class PriorityCache:
    """Short per-course cache. A fetch that started earlier never replaces a later one."""

    def __init__(self, ttl=CACHE_TTL_SECONDS, clock=time.monotonic):
        self.ttl, self.clock, self._lock, self._entries = ttl, clock, threading.Lock(), {}

    def get(self, key):
        with self._lock:
            entry = self._entries.get(key)
            if entry and self.clock() - entry["stored"] < self.ttl:
                return entry
            self._entries.pop(key, None)
            return None

    def put(self, key, started, fetched_at, value):
        with self._lock:
            current = self._entries.get(key)
            if current and current["started"] > started:
                return False
            self._entries[key] = {"started": started, "stored": self.clock(), "fetched_at": fetched_at, "value": value}
            return True

    def drop(self, key):
        with self._lock:
            self._entries.pop(key, None)

    def clear(self):
        with self._lock:
            self._entries.clear()


_cache = PriorityCache()


def _now_iso():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _cached_or_fetch(key, fetch, refresh):
    if not refresh:
        entry = _cache.get(key)
        if entry:
            return entry["value"], entry["fetched_at"], True
    started = _cache.clock()
    try:
        value = fetch()
    except Exception:  # noqa: BLE001 - one failing course must not hide the others.
        _cache.drop(key)  # never leave an older response behind a failed refresh
        raise
    fetched_at = _now_iso()
    _cache.put(key, started, fetched_at, value)
    return value, fetched_at, False


def get_priorities(refresh=False):
    """All current courses' assignments; failed courses are reported, never silently dropped."""
    courses, _, _ = _cached_or_fetch("courses", _load_courses, refresh)

    def load(course):
        try:
            return course, _cached_or_fetch(("course", course["id"]), lambda: _fetch_course(course), refresh), None
        except Exception as error:  # noqa: BLE001
            return course, None, error

    with ThreadPoolExecutor(max_workers=MAX_PARALLEL_COURSES) as pool:
        results = list(pool.map(load, courses))
    assignments, failed, stamps, any_cached = [], [], [], False
    for course, loaded, error in results:
        if error is not None:
            failed.append(course["id"])
            continue
        items, fetched_at, from_cache = loaded
        assignments.extend(items)
        stamps.append(fetched_at)
        any_cached = any_cached or from_cache
    return {
        "fetched_at": min(stamps) if stamps else _now_iso(),  # oldest data shown, stated honestly
        "cached": any_cached,
        "courses": [{"id": c["id"], "course_code": c.get("course_code"), "name": c.get("name")} for c in courses],
        "assignments": assignments,
        "failed_course_ids": failed,
    }
