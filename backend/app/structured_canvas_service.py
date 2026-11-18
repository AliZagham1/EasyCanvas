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


_WEEKDAYS = {"monday": 0, "mon": 0, "tuesday": 1, "tue": 1, "tues": 1, "wednesday": 2, "wed": 2, "thursday": 3, "thu": 3,
             "thur": 3, "thurs": 3, "friday": 4, "fri": 4, "saturday": 5, "sat": 5, "sunday": 6, "sun": 6}
_MONTHS = {"january": 1, "jan": 1, "february": 2, "feb": 2, "march": 3, "mar": 3, "april": 4, "apr": 4, "may": 5, "june": 6,
           "jun": 6, "july": 7, "jul": 7, "august": 8, "aug": 8, "september": 9, "sep": 9, "sept": 9, "october": 10, "oct": 10,
           "november": 11, "nov": 11, "december": 12, "dec": 12}
_NUMBER_WORDS = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
                 "nine": 9, "ten": 10, "eleven": 11, "twelve": 12}
_WEEKDAY_NAMES = "|".join(sorted(_WEEKDAYS, key=len, reverse=True))
_MONTH_NAMES = "|".join(sorted(_MONTHS, key=len, reverse=True))
_UNTIL = r"(?:by|before|until|till|through|thru|up\s+to|no\s+later\s+than)"
MAX_REFERENCE_LENGTH = 80
DATE_PHRASE_HELP = ("Please say today, tonight, tomorrow, a weekday (for example Friday or next Friday), this week, next week, "
                    "this weekend, in 3 days, a date such as Nov 20 or 11/20, or use \"by\" for everything up to a day "
                    "(for example by Friday).")


_QUALIFIER_WORDS = r"(?:by|before|until|till|through|thru|up\s+to|no\s+later\s+than)"
UPCOMING_DAYS = 14


def restore_date_qualifier(reference, question):
    """The model often rewrites "by Friday" as just "Friday". Put the qualifier back when the student's own words had it."""
    if not isinstance(reference, str) or not reference.strip() or not isinstance(question, str):
        return reference
    if re.match(rf"\s*{_QUALIFIER_WORDS}\b", reference, re.I):
        return reference
    match = re.search(rf"\b({_QUALIFIER_WORDS})\s+(?:the\s+)?{re.escape(reference.strip())}\b", question, re.I)
    return f"{re.sub(r'\s+', ' ', match.group(1).lower())} {reference.strip()}" if match else reference


def _clean(reference):
    text = re.sub(r"[^\w\s/\-]", " ", reference.casefold().replace("’", "'"))
    text = re.sub(r"\s+", " ", text).strip()
    previous = None
    while previous != text:                                   # leading filler: "due on the friday" -> "friday"
        previous = text
        text = re.sub(r"^(?:due|on|for|the|that\s+is|which\s+is|is|are)\s+", "", text)
    return text


def _single_day(text, today):
    """The one calendar day a phrase names, or None. `today` is a course-local date."""
    if text in ("today", "tonight"):
        return today
    if text == "tomorrow":
        return today + timedelta(days=1)
    if text in ("day after tomorrow", "the day after tomorrow"):
        return today + timedelta(days=2)
    if text == "yesterday":
        return today - timedelta(days=1)
    match = re.fullmatch(rf"(?:(this|next|coming)\s+)?({_WEEKDAY_NAMES})", text)
    if match:
        weekday = _WEEKDAYS[match.group(2)]
        if match.group(1) == "next":                          # the same weekday in the calendar week after this one
            return today - timedelta(days=today.weekday()) + timedelta(days=7 + weekday)
        return today + timedelta(days=(weekday - today.weekday()) % 7)
    match = re.fullmatch(r"in\s+(\d{1,3}|" + "|".join(_NUMBER_WORDS) + r")\s+(day|days|week|weeks)", text)
    if match:
        amount = int(match.group(1)) if match.group(1).isdigit() else _NUMBER_WORDS[match.group(1)]
        days = amount * (7 if match.group(2).startswith("week") else 1)
        if 1 <= days <= 120:
            return today + timedelta(days=days)
    return _calendar_date(text, today)


def _calendar_date(text, today):
    """ISO, M/D[/Y], 'Nov 20', '20 November', 'the 20th'. Undated years mean the nearest sensible year."""
    iso = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", text)
    month = day = year = None
    if iso:
        year, month, day = (int(part) for part in iso.groups())
    elif (m := re.fullmatch(r"(\d{1,2})/(\d{1,2})(?:/(\d{2}|\d{4}))?", text)):
        month, day = int(m.group(1)), int(m.group(2))
        year = (2000 + int(m.group(3)) if m.group(3) and len(m.group(3)) == 2 else int(m.group(3))) if m.group(3) else None
    elif (m := re.fullmatch(rf"({_MONTH_NAMES})\s+(\d{{1,2}})(?:st|nd|rd|th)?(?:\s+(\d{{4}}))?", text)):
        month, day, year = _MONTHS[m.group(1)], int(m.group(2)), int(m.group(3)) if m.group(3) else None
    elif (m := re.fullmatch(rf"(\d{{1,2}})(?:st|nd|rd|th)?\s+(?:of\s+)?({_MONTH_NAMES})(?:\s+(\d{{4}}))?", text)):
        day, month, year = int(m.group(1)), _MONTHS[m.group(2)], int(m.group(3)) if m.group(3) else None
    elif (m := re.fullmatch(r"(\d{1,2})(?:st|nd|rd|th)", text)):
        day = int(m.group(1))                                  # "the 20th": the next 20th on or after today
        for offset in range(0, 3):
            index = today.month - 1 + offset
            try:
                candidate = datetime(today.year + index // 12, index % 12 + 1, day).date()
            except ValueError:
                continue
            if candidate >= today:
                return candidate
        return None
    else:
        return None
    try:
        if year is None:
            candidate = datetime(today.year, month, day).date()
            if candidate < today - timedelta(days=183):
                candidate = datetime(today.year + 1, month, day).date()
            return candidate
        return datetime(year, month, day).date()
    except ValueError:
        return None


def date_window(reference, now, timezone_name):
    """Resolve a student's date phrase to a half-open, course-local (start, end) pair of aware datetimes.

    Anything unclear raises ValueError, so the student is asked to rephrase instead of getting a guessed window.
    """
    if not isinstance(reference, str) or not reference.strip() or len(reference) > MAX_REFERENCE_LENGTH:
        raise ValueError(DATE_PHRASE_HELP)
    zone = ZoneInfo(timezone_name)
    local_now = now.astimezone(zone)
    today = local_now.date()
    text = _clean(reference)
    start_of = lambda day: datetime.combine(day, datetime.min.time(), zone)
    monday = today - timedelta(days=today.weekday())

    def span(first, last_exclusive):
        return start_of(first), start_of(last_exclusive)

    if text in ("upcoming", "soon", "coming up", "due soon"):  # the next two weeks, from now
        return local_now, local_now + timedelta(days=UPCOMING_DAYS)
    if text == "tonight":                                      # the rest of today
        return local_now, start_of(today + timedelta(days=1))
    if text in ("this week", "next week", "last week", "the rest of the week", "rest of the week", "rest of this week",
                "end of the week", "end of week", "end of this week"):
        if text == "next week":
            return span(monday + timedelta(days=7), monday + timedelta(days=14))
        if text == "last week":
            return span(monday - timedelta(days=7), monday)
        if text == "this week":
            return span(monday, monday + timedelta(days=7))
        return span(today, monday + timedelta(days=7))        # rest of / end of the week: from today through Sunday
    if text in ("this month", "next month"):
        first = today.replace(day=1)
        if text == "next month":
            first = (first + timedelta(days=32)).replace(day=1)
        return span(first, (first + timedelta(days=32)).replace(day=1))
    if text in ("this weekend", "next weekend", "the weekend", "weekend"):
        saturday = monday + timedelta(days=5 + (7 if text == "next weekend" else 0))
        if today.weekday() == 6 and text != "next weekend":    # Sunday: the weekend that is already under way
            saturday = today - timedelta(days=1)
        return span(saturday, saturday + timedelta(days=2))
    match = re.fullmatch(rf"{_UNTIL}\s+(.+)", text)
    if match:                                                  # "by Friday": everything from today through that day
        limit = match.group(1)
        if limit in ("next week", "this week", "the end of the week", "end of the week", "end of week"):
            end = monday + timedelta(days=14 if limit == "next week" else 7)
            return span(today, end)
        day = _single_day(limit, today)
        if day is None:
            raise ValueError(DATE_PHRASE_HELP)
        before_only = text.startswith("before")
        return span(today, day if before_only else day + timedelta(days=1))
    day = _single_day(text, today)
    if day is None:
        raise ValueError(DATE_PHRASE_HELP)
    return span(day, day + timedelta(days=1))


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
