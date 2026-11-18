"""Canvas facts for the Priorities page. Ranking itself lives in frontend/lib/priorities.ts.

This module only normalizes read-only Canvas assignment and submission data. It never writes to
Canvas, uses no LLM, and does not compute grades.
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


def normalize_assignment(course, raw):
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
    }


def build_course_items(course, raw_assignments):
    return [normalize_assignment(course, raw)
            for raw in raw_assignments if raw.get("published") is not False and raw.get("id") is not None]


# ---------------------------------------------------------------- Canvas access (read-only)
def _fetch_assignments(course_id):
    url = f"{canvas_service.CANVAS_URL}/api/v1/courses/{course_id}/assignments"
    return canvas_service._get_paginated(url, {"include[]": "submission"})


def _load_courses():
    return canvas_service.get_current_courses()


def _fetch_course(course):
    return build_course_items(course, _fetch_assignments(course["id"]))


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
