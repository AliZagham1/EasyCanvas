"""Class schedule: confirmed meeting times, plus rules-based suggestions read from the syllabus.

No AI and no network. Suggestions are never trusted data: only meetings the student confirms (or enters
manually) are stored as `confirmed`, and a suggestion always carries the quoted syllabus line it came from.
"""

import hashlib
import re
import uuid
from datetime import datetime, timezone

DAYS = ("MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN")
KINDS = ("lecture", "lab", "discussion", "other")
TIME_PATTERN = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
MAX_LOCATION = 120
MAX_QUOTE = 240

# ---------------------------------------------------------------- days
_NAMES = {
    "MON": ("monday", "mondays", "mon", "mo"), "TUE": ("tuesday", "tuesdays", "tues", "tue", "tu"),
    "WED": ("wednesday", "wednesdays", "wed", "we"), "THU": ("thursday", "thursdays", "thurs", "thur", "thu", "th"),
    "FRI": ("friday", "fridays", "fri", "fr"), "SAT": ("saturday", "saturdays", "sat", "sa"),
    "SUN": ("sunday", "sundays", "sun", "su"),
}
_DAY_WORDS = {word: day for day, words in _NAMES.items() for word in words}
_COMPACT = {"mwf": ("MON", "WED", "FRI"), "mw": ("MON", "WED"), "wf": ("WED", "FRI"), "mf": ("MON", "FRI"),
            "tth": ("TUE", "THU"), "tr": ("TUE", "THU"), "tuth": ("TUE", "THU"), "mwth": ("MON", "WED", "THU"),
            "mtwth": ("MON", "TUE", "WED", "THU"), "mtwthf": ("MON", "TUE", "WED", "THU", "FRI")}
_LETTERS = {"m": "MON", "t": "TUE", "w": "WED", "r": "THU", "f": "FRI"}
# Words allowed around the day tokens ("Class meets on ..."). Any other word means this is a sentence, not a meeting line.
_FILLER = {"class", "classes", "meet", "meets", "meeting", "meetings", "time", "times", "on", "every", "each", "lecture",
           "lectures", "lab", "labs", "days", "day", "at", "from", "the", "is", "are", "held", "section", "in", "person",
           "location", "room", "rm", "place", "of", "mode", "instruction", "online", "via", "and"}
_SEPARATORS = re.compile(r"[&/,;+()\[\]:.\-–—]+")

ANCHOR = re.compile(r"time and place of class meetings?|class meetings?\s*:|meeting times?|class times?|lecture times?|"
                    r"meeting days?|class schedule|meeting location|class days?", re.I)
EXCLUDED = re.compile(r"office|hours|appointment|exam|final|midterm|quiz|\bdue\b|deadline|\btest\b|\bta\b|tutor|review session",
                      re.I)
ONLINE = re.compile(r"microsoft teams|\bteams\b|\bzoom\b|\bonline\b|webex|virtual", re.I)

TIME_RANGE = re.compile(
    r"(?P<h1>\d{1,2})(?:[:.](?P<m1>\d{2}))?\s*(?P<p1>[ap]\.?\s?m\.?)?\s*(?:-|–|—|to|until)\s*"
    r"(?P<h2>\d{1,2})(?:[:.](?P<m2>\d{2}))?\s*(?P<p2>[ap]\.?\s?m\.?)?", re.I)
ROOM = re.compile(r"\b([A-Z]{2,5})\s?-?\s?(\d{2,4}[A-Z]?)\b")
ROOM_WORD = re.compile(r"\b(?:room|rm\.?)\s*#?\s*([A-Za-z]{0,4}\s?-?\d{1,4}[A-Za-z]?)\b", re.I)
LOCATION_LINE = re.compile(r"^\s*location\s*:\s*(.+)$", re.I)
SECTION_MARKER = re.compile(r"(?<!\d)[A-Z]{2,5}\s?\d{4}[-\s](\d{3})(?!\d)|\bsection\s*0*(\d{1,3})\b", re.I)


class ScheduleError(ValueError):
    """A request that cannot be saved. The message is safe to show to the student."""


def parse_days(text):
    """Day codes (Mon..Sun order) if `text` is only day tokens and filler words, else None."""
    tokens = _SEPARATORS.sub(" ", text.lower()).split()
    found, letters = [], []
    for token in tokens:
        if token in _FILLER:
            continue
        if token in _DAY_WORDS:
            found.append(_DAY_WORDS[token])
        elif token in _COMPACT:
            found.extend(_COMPACT[token])
        elif re.fullmatch(r"(?:mo|tu|we|th|fr|sa|su){2,}", token):          # MoWe, TuTh, MoWeFr
            found.extend(_DAY_WORDS[token[i:i + 2]] for i in range(0, len(token), 2))
        elif token in _LETTERS:
            letters.append(_LETTERS[token])
        else:
            return None
    if letters:
        if len(letters) + len(found) < 2:      # a lone "T" or "W" is far too ambiguous
            return None
        found.extend(letters)
    unique = [day for day in DAYS if day in found]
    return unique or None


def _clock(hour, minute, meridiem):
    return (hour % 12) * 60 + minute + (720 if meridiem == "pm" else 0)


def parse_time_range(match):
    """(start, end) as 'HH:MM' strings, or None when the range is ambiguous or invalid. Never guesses am/pm blindly."""
    h1, h2 = int(match["h1"]), int(match["h2"])
    m1, m2 = int(match["m1"] or 0), int(match["m2"] or 0)
    if m1 > 59 or m2 > 59:
        return None
    p1 = (match["p1"] or "")[:1].lower() or None
    p2 = (match["p2"] or "")[:1].lower() or None
    if p1 is None and p2 is None:
        # 24-hour clock is accepted only when it is unmistakable: both times have minutes and one is past 12.
        if match["m1"] is None or match["m2"] is None or max(h1, h2) < 13 or max(h1, h2) > 23:
            return None
        start, end = h1 * 60 + m1, h2 * 60 + m2
    else:
        if not (1 <= h1 <= 12 and 1 <= h2 <= 12):
            return None
        p2 = "am" if (p2 or p1) == "a" else "pm"
        inherited = p1 is None
        p1 = p2 if inherited else ("am" if p1 == "a" else "pm")
        start, end = _clock(h1, m1, p1), _clock(h2, m2, p2)
        if inherited and start >= end and p2 == "pm":      # "11-12:20 pm" means 11:00 am
            start = _clock(h1, m1, "am")
            if start < 6 * 60:                             # a 3:30 am class is not plausible: reject, never guess
                return None
    if start >= end:
        return None
    return f"{start // 60:02d}:{start % 60:02d}", f"{end // 60:02d}:{end % 60:02d}"


def _location(line, excluded_prefixes):
    for match in ROOM.finditer(line):
        if match.group(1) not in excluded_prefixes:
            return match.group(0), f"{match.group(1)} {match.group(2)}"
    word = ROOM_WORD.search(line)
    if word:
        return word.group(0), f"Room {word.group(1).strip()}"
    return None, None


def parse_line(line, excluded_prefixes=()):
    """Parse one syllabus line into {'days', 'times', 'location'}; each part may be None."""
    if EXCLUDED.search(line):
        return None
    working = line
    times = None
    for match in TIME_RANGE.finditer(line):
        parsed = parse_time_range(match)
        if parsed:
            times = parsed
            working = working.replace(match.group(0), " ", 1)
            break
    room_text, location = _location(working, excluded_prefixes)
    if room_text:
        working = working.replace(room_text, " ", 1)
    if location is None and ONLINE.search(line):
        location = "Online"
    working = ONLINE.sub(" ", working)
    working = re.sub(r"location\s*:|instruction mode\s*:.*", " ", working, flags=re.I)
    days = parse_days(working) if working.strip() else None
    if days is None and times is None:
        return None
    return {"days": days, "times": times, "location": location}


def fingerprint(course_id, days, start, end):
    return hashlib.sha1(f"{course_id}|{','.join(days)}|{start}|{end}".encode()).hexdigest()[:16]


def _section_of(course_code):
    match = re.search(r"-(\d{3})$", (course_code or "").strip())
    return int(match.group(1)) if match else None


def _other_section(lines, position, my_section):
    """True when the nearest section marker just above this line names a different section than the student's."""
    if my_section is None:
        return False
    for earlier in range(position, max(position - 4, -1), -1):
        found = SECTION_MARKER.search(lines[earlier])
        if found:
            return int(found.group(1) or found.group(2)) != my_section
    return False


def extract_suggestions(text, course_id, *, course_code=None):
    """Candidate class meetings from syllabus text. Returns {'suggestions': [...], 'hints': [...]}.

    A syllabus that lists several sections is filtered to the student's own section (from the course code).
    """
    prefixes = set(re.findall(r"(?:^|[-\s])([A-Z]{2,5})(?=[-\s]?\d)", course_code or ""))
    my_section = _section_of(course_code)
    time_locations = {}
    lines = [re.sub(r"\s+", " ", raw).strip() for raw in text.splitlines()]
    lines = [line for line in lines if line]
    suggestions, partial_days, partial_times, seen = [], [], [], set()
    for index, line in enumerate(lines):
        if not ANCHOR.search(line):
            continue
        window = range(index, min(index + 9, len(lines)))
        window_location = None
        for position in window:
            match = LOCATION_LINE.match(lines[position])
            if match and not EXCLUDED.search(lines[position]):
                window_location = match.group(1).strip()[:MAX_LOCATION]
        for position in window:
            current = lines[position]
            previous = lines[position - 1] if position else ""
            if EXCLUDED.search(previous) and not ANCHOR.search(previous) and not ANCHOR.search(current):
                continue                   # a bare value under an "Office Hours" label (a labeled line is judged on its own)
            parsed = parse_line(current, prefixes)
            if not parsed or _other_section(lines, position, my_section):
                continue
            location = parsed["location"] or window_location
            if parsed["times"] and not parsed["days"] and location:
                time_locations.setdefault(parsed["times"], location)
            quote = current[:MAX_QUOTE]
            if parsed["days"] and parsed["times"]:
                key = (tuple(parsed["days"]), parsed["times"])
                if key in seen:
                    continue
                seen.add(key)
                suggestions.append({
                    "fingerprint": fingerprint(course_id, parsed["days"], *parsed["times"]),
                    "days": parsed["days"], "start_time": parsed["times"][0], "end_time": parsed["times"][1],
                    "location": location, "kind": "lab" if re.search(r"\blab\b", current, re.I) else "lecture",
                    "source_text": quote, "confidence": "high" if ANCHOR.search(lines[index]) else "medium"})
            elif parsed["days"]:
                partial_days.append((parsed["days"], quote, location))
            elif parsed["times"]:
                partial_times.append((parsed["times"], quote, location))
    for suggestion in suggestions:           # a room given next to a time-only line ("Meeting Time ... Location: NH 229")
        if not suggestion["location"]:
            suggestion["location"] = time_locations.get((suggestion["start_time"], suggestion["end_time"]))
    # Days on one line and a time on another, within the same section: offer a combined, lower-confidence suggestion.
    if not suggestions and len(partial_days) >= 1 and len(partial_times) >= 1:
        days, day_quote, day_location = partial_days[0]
        times, time_quote, time_location = partial_times[0]
        suggestions.append({
            "fingerprint": fingerprint(course_id, days, *times), "days": days, "start_time": times[0], "end_time": times[1],
            "location": day_location or time_location, "kind": "lecture",
            "source_text": f"{day_quote} / {time_quote}"[:MAX_QUOTE], "confidence": "medium"})
        partial_days = []
    hints = [] if suggestions else [{"days": days, "source_text": quote} for days, quote, _ in partial_days[:1]]
    return {"suggestions": suggestions, "hints": hints}


# ---------------------------------------------------------------- stored meetings
def _now():
    return datetime.now(timezone.utc).isoformat()


def validate_meeting(data):
    """Return normalized meeting fields or raise ScheduleError with a safe message."""
    days = data.get("days")
    if not isinstance(days, list) or not days or any(day not in DAYS for day in days):
        raise ScheduleError("Choose at least one day of the week.")
    start, end = data.get("start_time"), data.get("end_time")
    if not isinstance(start, str) or not TIME_PATTERN.match(start) or not isinstance(end, str) or not TIME_PATTERN.match(end):
        raise ScheduleError("Enter the start and end time as HH:MM (24-hour).")
    if start >= end:
        raise ScheduleError("The end time must be after the start time.")
    kind = data.get("kind") or "lecture"
    if kind not in KINDS:
        raise ScheduleError("Choose a valid meeting type.")
    location = data.get("location")
    if location is not None:
        if not isinstance(location, str):
            raise ScheduleError("Location must be text.")
        location = location.strip() or None
        if location and len(location) > MAX_LOCATION:
            raise ScheduleError(f"Location must be at most {MAX_LOCATION} characters.")
    return {"days": [day for day in DAYS if day in days], "start_time": start, "end_time": end, "kind": kind,
            "location": location}


def create_meeting(store, course_id, data, *, source="manual", source_text=None, fingerprint_value=None):
    fields = validate_meeting(data)
    record = {"meeting_id": uuid.uuid4().hex, "course_id": course_id, "status": "confirmed", "source": source,
              "source_text": (source_text or None) and str(source_text)[:MAX_QUOTE],
              "fingerprint": fingerprint_value or fingerprint(course_id, fields["days"], fields["start_time"], fields["end_time"]),
              "created_at": _now(), "updated_at": _now(), **fields}
    store.save_class_meeting(record)
    return record


def update_meeting(store, meeting_id, data):
    existing = store.get_class_meeting(meeting_id)
    if not existing or existing["status"] != "confirmed":
        raise LookupError("Meeting not found.")
    fields = validate_meeting({**existing, **data})
    record = {**existing, **fields, "updated_at": _now()}   # the fingerprint stays: it remembers which suggestion this came from
    store.save_class_meeting(record)
    return record


def dismiss_suggestion(store, course_id, fingerprint_value):
    if not isinstance(fingerprint_value, str) or not re.fullmatch(r"[0-9a-f]{16}", fingerprint_value):
        raise ScheduleError("That suggestion could not be identified.")
    record = {"meeting_id": uuid.uuid4().hex, "course_id": course_id, "status": "dismissed", "kind": "lecture",
              "days": ["MON"], "start_time": "00:00", "end_time": "00:01", "location": None, "source": "syllabus",
              "source_text": None, "fingerprint": fingerprint_value, "created_at": _now(), "updated_at": _now()}
    store.save_class_meeting(record)


def _course_infos(store):
    infos = []
    for course_id in store.saved_current_course_ids():
        snapshot = store.load_course(course_id) or {}
        meta = (snapshot.get("course") or {}).get("metadata") or {}
        infos.append({"course_id": course_id, "course_code": meta.get("course_code"), "name": meta.get("name"),
                      "time_zone": meta.get("time_zone")})
    return infos


def syllabus_text(store, course_id):
    chunks = [chunk for chunk in store.load_chunks(course_id)
              if str(chunk.get("document_key", "")).startswith(("syllabus_file", "syllabus:"))]
    chunks.sort(key=lambda chunk: (chunk["document_key"], chunk["chunk_index"]))
    return "\n\n".join(chunk["text"] for chunk in chunks)


def build_schedule(store):
    """Everything the Class Schedule page shows, from saved data only (no Canvas request, no waiting for sync)."""
    stored = store.list_class_meetings()
    courses = []
    for info in _course_infos(store):
        course_id = info["course_id"]
        confirmed = [m for m in stored if m["course_id"] == course_id and m["status"] == "confirmed"]
        dismissed = {m["fingerprint"] for m in stored if m["course_id"] == course_id and m["status"] == "dismissed"}
        known = {m["fingerprint"] for m in confirmed}
        known |= {fingerprint(course_id, m["days"], m["start_time"], m["end_time"]) for m in confirmed}
        found = extract_suggestions(syllabus_text(store, course_id), course_id, course_code=info["course_code"])
        suggestions = [s for s in found["suggestions"] if s["fingerprint"] not in known | dismissed]
        courses.append({**info, "meetings": confirmed, "suggestions": suggestions,
                        "hints": [] if confirmed or suggestions else found["hints"],
                        "dismissed_count": len(dismissed)})
    return {"courses": courses}
