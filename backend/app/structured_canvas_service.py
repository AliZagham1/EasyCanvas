"""Use actual Canvas assignment fields, separately from document vector search."""

import copy
from datetime import datetime, timedelta, timezone
import hashlib
from html import unescape
import json
import math
import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from backend.app import canvas_service


def date_window(reference, now, timezone_name):
    """Resolve supported calendar references with aware, course-local datetimes."""
    zone = ZoneInfo(timezone_name)
    today = now.astimezone(zone).date()
    value = re.sub(r"^due\s+", "", reference.strip().casefold())
    days = 1
    if value == "today":
        start = today
    elif value == "tomorrow":
        start = today + timedelta(days=1)
    elif value in ("this week", "next week"):
        start = today - timedelta(days=today.weekday()) + timedelta(days=7 if value == "next week" else 0)
        days = 7
    elif value in ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"):
        weekday = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday").index(value)
        start = today + timedelta(days=(weekday - today.weekday()) % 7)
    elif re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        start = datetime.fromisoformat(value).date()
    else:
        raise ValueError("Use today, tomorrow, a weekday, this week, next week, or YYYY-MM-DD.")
    return (datetime.combine(start, datetime.min.time(), zone),
            datetime.combine(start + timedelta(days=days), datetime.min.time(), zone))


def course_timezone(snapshot):
    value = snapshot.get("course", {}).get("metadata", {}).get("time_zone") or "UTC"
    try:
        ZoneInfo(value)
    except (ValueError, ZoneInfoNotFoundError):
        value = "UTC"
    return value


def due_in_window(raw, window):
    due = raw.get("due_at")
    if not isinstance(due, str):
        return False
    parsed = datetime.fromisoformat(due.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Canvas deadline must have a timezone.")
    return window[0] <= parsed < window[1]


def current_assignment(course_id, record):
    raw = canvas_service.get_assignment(course_id, record["canvas_id"])
    if (not isinstance(raw, dict) or raw.get("id") != record["canvas_id"]
            or raw.get("course_id", course_id) != course_id):
        raise ValueError("Canvas assignment identity did not match.")
    return {**record, "title": raw.get("name") or record.get("title"), "metadata": raw}


def current_assignments(course_id):
    records = []
    for raw in canvas_service.get_assignments(course_id):
        if not isinstance(raw, dict) or type(raw.get("id")) is not int or raw.get("course_id", course_id) != course_id:
            raise ValueError("Canvas assignment identity did not match.")
        records.append({"canvas_id": raw["id"], "course_id": course_id, "title": raw.get("name"),
                        "source_type": "assignment", "url": raw.get("html_url"), "metadata": raw})
    return records


def _origin(course_id, record, kind, locations):
    return {"course_id": course_id, "document_key": f"assignment:{record['canvas_id']}",
            "canvas_id": record["canvas_id"], "source_type": kind, "source_kind": "structured_canvas",
            "title": record.get("title"), "url": record.get("url") or record.get("metadata", {}).get("html_url"),
            "chunk_index": 0, "locations": locations, "status": "ok", "warnings": [],
            "source_quality": {"flags": [], "policy_retrieval_eligible": True}}


def evidence_item(text, origin, data):
    """Wrap backend-owned structured facts in the existing citation/evidence shape."""
    return {"score": None, "text_hash": hashlib.sha256(text.encode()).hexdigest(), "text": text,
            "sources": [origin], "structured_data": data}


def rubric_evidence(course_id, record, saved_at):
    """Expose full actual criteria/ratings/points, never synthesize a missing rubric."""
    raw = record.get("metadata") or {}
    rubric = raw.get("rubric")
    if not isinstance(rubric, list) or not rubric:
        return []
    results = []
    for index, criterion in enumerate(rubric):
        if not isinstance(criterion, dict) or criterion.get("ratings") is not None and not isinstance(criterion["ratings"], list):
            raise ValueError("Saved rubric has an invalid shape.")
        ratings = criterion.get("ratings") or []
        if any(not isinstance(rating, dict) for rating in ratings):
            raise ValueError("Saved rubric has invalid rating entries.")
        data = {key: copy.deepcopy(criterion.get(key)) for key in (
            "id", "description", "long_description", "points", "ignore_for_scoring", "criterion_use_range", "ratings")}
        for value in [criterion.get("points"), *(rating.get("points") for rating in ratings)]:
            if value is not None and (type(value) not in (int, float) or not math.isfinite(value)):
                raise ValueError("Saved rubric has invalid points.")
        data.update(assignment_id=record["canvas_id"], assignment_title=record.get("title"), snapshot_at=saved_at,
                    use_rubric_for_grading=raw.get("use_rubric_for_grading"),
                    rubric_settings=copy.deepcopy(raw.get("rubric_settings") or {}))
        text = "Saved Canvas grading rubric (not the student's earned score):\n" + unescape(json.dumps(data, ensure_ascii=False))
        origin = _origin(course_id, record, "assignment_rubric", [{"kind": "rubric_criterion",
                         "criterion_id": criterion.get("id"), "criterion_name": criterion.get("description")}])
        origin["chunk_index"] = index
        results.append(evidence_item(text, origin, data))
    return results


def current_fact_evidence(course_id, records, requested, timezone_name, now):
    """Produce exact fact text in backend code; these responses need no answer LLM."""
    results = []
    for record in records:
        raw = record["metadata"]
        title = record.get("title") or "This assignment"
        data = {"assignment_id": record["canvas_id"], "assignment_title": title,
                "checked_at": now.isoformat(), "timezone": timezone_name}
        facts = []
        if "deadline" in requested:
            value = raw.get("due_at")
            data["due_at"] = value
            if value:
                due = datetime.fromisoformat(value.replace("Z", "+00:00"))
                if due.tzinfo is None:
                    raise ValueError("Canvas deadline must have a timezone.")
                facts.append(f"{title}: deadline {due.astimezone(ZoneInfo(timezone_name)).strftime('%Y-%m-%d %H:%M %Z')} ({timezone_name}).")
            else:
                facts.append(f"{title}: Canvas does not provide a due date.")
        if "submission_status" in requested:
            submission = raw.get("submission") or {}
            data["submission"] = {key: submission.get(key) for key in ("workflow_state", "submitted_at", "late", "missing")}
            state = submission.get("workflow_state")
            facts.append(f"{title}: Canvas submission status is {state}." if state else f"{title}: Canvas did not return a submission status.")
        if "points" in requested:
            points = raw.get("points_possible")
            if points is not None and (type(points) not in (int, float) or not math.isfinite(points)):
                raise ValueError("Canvas returned invalid assignment points.")
            data["points_possible"] = points
            facts.append(f"{title}: {points:g} points possible." if points is not None else f"{title}: Canvas did not return points possible.")
        if "assignment_title" in requested or not facts:
            facts.append(f"Assignment: {title}.")
        results.append(evidence_item("\n".join(facts), _origin(course_id, record, "assignment_facts", [{"kind": "canvas_assignment"}]), data))
    return results
