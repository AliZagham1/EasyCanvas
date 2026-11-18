"""Study plans (Stage A): a student's own steps for an assignment or exam, and a rules-based search for free time.

No AI and no network of its own. Rules:
- A plan's deadline comes from Canvas (via the Priorities data) or from the student for an exam or other custom target;
  it is never guessed.
- Free time is what is left of the student's study hours after confirmed class meetings (and study blocks) and after
  study sessions already saved for any plan. Nothing is scheduled over a commitment.
- Times are only *proposed*. They are saved when the student accepts them, and the server re-checks every one.
"""

import re
import uuid
from collections import defaultdict
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

DAYS = ("MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN")
TIME_PATTERN = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
MAX_TITLE = 120
MAX_ACTIVITIES = 30
MIN_MINUTES, MAX_MINUTES = 10, 240
ALIGN_MINUTES = 5          # session starts snap to 5-minute marks
MIN_SLOT = 30              # shorter gaps are not offered
BREAK_MINUTES = 10         # a pause between two sessions in the same gap
DEFAULT_PREFERENCES = {"day_start": "09:00", "day_end": "21:00", "max_minutes_per_day": 240, "buffer_minutes": 120}


class PlanError(ValueError):
    """A request that cannot be saved. The message is safe to show to the student."""


class PlanConflict(PlanError):
    """The chosen time overlaps something the student already has. `conflicts` lists what, so the student can decide."""

    def __init__(self, message, conflicts):
        super().__init__(message)
        self.conflicts = conflicts


def _now():
    return datetime.now(timezone.utc)


def _iso(moment):
    return moment.astimezone(timezone.utc).isoformat()


def _parse_instant(value, label):
    if not isinstance(value, str) or not value:
        raise PlanError(f"{label} is required.")
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise PlanError(f"{label} is not a valid date and time.") from None
    if moment.tzinfo is None:
        raise PlanError(f"{label} must include a time zone.")
    return moment.astimezone(timezone.utc)


def _minutes_of(clock):
    return int(clock[:2]) * 60 + int(clock[3:5])


# ---------------------------------------------------------------- preferences
def validate_preferences(data):
    merged = {**DEFAULT_PREFERENCES, **{key: data[key] for key in DEFAULT_PREFERENCES if key in data}}
    for key in ("day_start", "day_end"):
        if not isinstance(merged[key], str) or not TIME_PATTERN.match(merged[key]):
            raise PlanError("Enter study hours as HH:MM (24-hour).")
    if _minutes_of(merged["day_end"]) - _minutes_of(merged["day_start"]) < 60:
        raise PlanError("Study hours must cover at least one hour.")
    cap, buffer = merged["max_minutes_per_day"], merged["buffer_minutes"]
    if type(cap) is not int or not 30 <= cap <= 720:
        raise PlanError("Daily study time must be between 30 and 720 minutes.")
    if type(buffer) is not int or not 0 <= buffer <= 1440:
        raise PlanError("The buffer before a deadline must be between 0 and 1440 minutes.")
    return merged


def get_preferences(store):
    saved = store.load_settings().get("study_preferences")
    try:
        return validate_preferences(saved if isinstance(saved, dict) else {})
    except PlanError:
        return dict(DEFAULT_PREFERENCES)      # a corrupt saved value falls back to the defaults


def set_preferences(store, data):
    prefs = validate_preferences(data)
    store.update_settings({"study_preferences": prefs})
    return prefs


# ---------------------------------------------------------------- time zone
def _zone(name):
    try:
        return ZoneInfo(name) if name else datetime.now().astimezone().tzinfo
    except Exception:  # noqa: BLE001 - an unknown zone name falls back to this computer's zone
        return datetime.now().astimezone().tzinfo


def _at(day, minutes, zone):
    """The wall-clock moment `minutes` after local midnight on `day`, as an aware UTC datetime."""
    return datetime.combine(day, time(minutes // 60, minutes % 60), tzinfo=zone).astimezone(timezone.utc)


def _ceil(moment, step):
    """Round up to the next `step`-minute mark (a moment already on a mark is unchanged)."""
    base = moment.replace(second=0, microsecond=0)
    if base != moment:
        base += timedelta(minutes=1)
    return base + timedelta(minutes=(-base.minute) % step)


# ---------------------------------------------------------------- free time
def _subtract(window, busy):
    """The parts of (start, end) not covered by any (start, end) in `busy`."""
    pieces, cursor = [], window[0]
    for start, end in sorted(busy):
        if end <= cursor or start >= window[1]:
            continue
        if start > cursor:
            pieces.append((cursor, min(start, window[1])))
        cursor = max(cursor, end)
    if cursor < window[1]:
        pieces.append((cursor, window[1]))
    return pieces


def _meeting_spans(meetings, day, zone):
    weekday = DAYS[day.weekday()]
    return [(_at(day, _minutes_of(m["start_time"]), zone), _at(day, _minutes_of(m["end_time"]), zone))
            for m in meetings if weekday in m["days"]]


def free_slots(now, due, meetings, sessions, prefs, zone):
    """Open gaps between `now` and (due - buffer), inside the student's daily study hours.

    `meetings` are confirmed weekly meetings (wall-clock in `zone`); `sessions` are saved (start, end) study sessions.
    Each returned gap lies inside one local day and is at least MIN_SLOT minutes long.
    """
    limit = due - timedelta(minutes=prefs["buffer_minutes"])
    begin = _ceil(now, ALIGN_MINUTES)
    if limit <= begin:
        return []
    slots, day = [], begin.astimezone(zone).date()
    last = limit.astimezone(zone).date()
    while day <= last:
        window = (max(_at(day, _minutes_of(prefs["day_start"]), zone), begin),
                  min(_at(day, _minutes_of(prefs["day_end"]), zone), limit))
        if window[0] < window[1]:
            for start, end in _subtract(window, _meeting_spans(meetings, day, zone) + list(sessions)):
                start = _ceil(start, ALIGN_MINUTES)
                if (end - start) >= timedelta(minutes=MIN_SLOT):
                    slots.append((start, end))
        day += timedelta(days=1)
    return slots


def place_activities(activities, slots, sessions, prefs, zone):
    """First-fit: each activity, in order, goes to the earliest gap that holds it without passing the daily limit."""
    gaps = [[start, end] for start, end in slots]
    used = defaultdict(int)
    for start, end in sessions:
        used[start.astimezone(zone).date()] += int((end - start).total_seconds() // 60)
    cap = prefs["max_minutes_per_day"]
    placements, unplaced = [], []
    for activity in activities:
        need = timedelta(minutes=activity["minutes"])
        fits_somewhere = any(end - start >= need for start, end in gaps)
        chosen = None
        for gap in gaps:
            day = gap[0].astimezone(zone).date()
            if gap[1] - gap[0] >= need and used[day] + activity["minutes"] <= cap:
                chosen = gap
                break
        if chosen is None:
            unplaced.append({"activity_id": activity["activity_id"], "reason": (
                "Your daily study limit leaves no room before the deadline." if fits_somewhere
                else f"No free gap of {activity['minutes']} minutes before the deadline.")})
            continue
        start = chosen[0]
        placements.append({"activity_id": activity["activity_id"], "start_at": _iso(start), "end_at": _iso(start + need)})
        used[start.astimezone(zone).date()] += activity["minutes"]
        chosen[0] = _ceil(start + need + timedelta(minutes=BREAK_MINUTES), ALIGN_MINUTES)
        if chosen[1] - chosen[0] < timedelta(minutes=MIN_SLOT):
            gaps.remove(chosen)
    return placements, unplaced


# ---------------------------------------------------------------- plans
def _course_info(store, course_id):
    if course_id not in store.saved_current_course_ids():
        raise LookupError("Course not found. Sync Canvas first.")
    meta = ((store.load_course(course_id) or {}).get("course") or {}).get("metadata") or {}
    return {"course_code": meta.get("course_code"), "course_name": meta.get("name"), "time_zone": meta.get("time_zone")}


def _clean_title(value, label="Title"):
    if not isinstance(value, str) or not value.strip():
        raise PlanError(f"{label} is required.")
    value = " ".join(value.split())
    if len(value) > MAX_TITLE:
        raise PlanError(f"{label} must be at most {MAX_TITLE} characters.")
    return value


def _clean_minutes(value):
    if type(value) is not int or not MIN_MINUTES <= value <= MAX_MINUTES:
        raise PlanError(f"Enter between {MIN_MINUTES} and {MAX_MINUTES} minutes.")
    return value


def create_plan(store, data, *, fetch_assignment, now=None):
    """A plan for a Canvas assignment (title and deadline come from Canvas) or for an exam/other item the student dates."""
    now = now or _now()
    course_id = data.get("course_id")
    _course_info(store, course_id)
    assignment_id = data.get("assignment_id")
    if assignment_id is not None:
        item = fetch_assignment(course_id, assignment_id)
        if not item:
            raise LookupError("Assignment not found in Canvas.")
        if item.get("submission_status") in ("submitted", "graded", "excused"):
            raise PlanError("That assignment is already complete, so there is nothing to plan.")
        if not item.get("due_at"):
            raise PlanError("That assignment has no due date in Canvas. Plan it as a custom item and enter your own date.")
        title, due, kind = _clean_title(item.get("name") or "Assignment"), _parse_instant(item["due_at"], "Due date"), "assignment"
    else:
        title, due, kind = _clean_title(data.get("title")), _parse_instant(data.get("due_at"), "Date"), "custom"
    if due <= now:
        raise PlanError("That deadline has already passed.")
    stamp = _iso(now)
    record = {"plan_id": uuid.uuid4().hex, "course_id": course_id, "kind": kind, "assignment_id": assignment_id,
              "title": title, "due_at": _iso(due), "created_at": stamp, "updated_at": stamp}
    store.save_study_plan(record)
    return record


def _plan_or_404(store, plan_id):
    plan = store.get_study_plan(plan_id)
    if not plan:
        raise LookupError("Plan not found.")
    return plan


def add_activity(store, plan_id, data, *, now=None):
    _plan_or_404(store, plan_id)
    existing = store.list_plan_activities(plan_id)
    if len(existing) >= MAX_ACTIVITIES:
        raise PlanError(f"A plan can have at most {MAX_ACTIVITIES} steps.")
    stamp = _iso(now or _now())
    record = {"activity_id": uuid.uuid4().hex, "plan_id": plan_id, "position": max((a["position"] for a in existing), default=-1) + 1,
              "title": _clean_title(data.get("title"), "Step"), "minutes": _clean_minutes(data.get("minutes")),
              "done": False, "start_at": None, "end_at": None, "created_at": stamp, "updated_at": stamp}
    store.save_plan_activity(record)
    return record


def update_activity(store, plan_id, activity_id, data, *, now=None):
    _plan_or_404(store, plan_id)
    activity = store.get_plan_activity(activity_id)
    if not activity or activity["plan_id"] != plan_id:
        raise LookupError("Step not found.")
    changed = dict(activity)
    if "title" in data:
        changed["title"] = _clean_title(data["title"], "Step")
    if "minutes" in data:
        changed["minutes"] = _clean_minutes(data["minutes"])
        if changed["minutes"] != activity["minutes"]:
            changed["start_at"] = changed["end_at"] = None      # the saved time no longer matches the length
    if "done" in data:
        if type(data["done"]) is not bool:
            raise PlanError("Done must be true or false.")
        changed["done"] = data["done"]
    changed["updated_at"] = _iso(now or _now())
    store.save_plan_activity(changed)
    return changed


def delete_activity(store, plan_id, activity_id):
    activity = store.get_plan_activity(activity_id)
    if not activity or activity["plan_id"] != plan_id or not store.delete_plan_activity(activity_id):
        raise LookupError("Step not found.")


def delete_plan(store, plan_id):
    if not store.delete_study_plan(plan_id):
        raise LookupError("Plan not found.")


def clear_time(store, plan_id, activity_id, *, now=None):
    activity = store.get_plan_activity(activity_id)
    if not activity or activity["plan_id"] != plan_id:
        raise LookupError("Step not found.")
    store.save_plan_activity({**activity, "start_at": None, "end_at": None, "updated_at": _iso(now or _now())})


# ---------------------------------------------------------------- proposing and accepting times
def _confirmed_meetings(store, course_ids=None):
    return [m for m in store.list_class_meetings("confirmed") if course_ids is None or m["course_id"] in course_ids]


def _saved_sessions(store, *, after=None):
    spans = []
    for activity in store.list_plan_activities():
        if not activity["start_at"] or activity["done"]:
            continue
        start, end = _parse_instant(activity["start_at"], "Start"), _parse_instant(activity["end_at"], "End")
        if after is None or end > after:
            spans.append((start, end))
    return spans


def suggest_times(store, plan_id, *, now=None):
    """Propose times for this plan's unscheduled steps. Reads and computes only; saves nothing."""
    now = now or _now()
    plan = _plan_or_404(store, plan_id)
    zone = _zone(_course_info(store, plan["course_id"])["time_zone"])
    prefs = get_preferences(store)
    due = _parse_instant(plan["due_at"], "Due date")
    waiting = [a for a in store.list_plan_activities(plan_id) if not a["done"] and not a["start_at"]]
    sessions = _saved_sessions(store, after=now)
    slots = free_slots(now, due, _confirmed_meetings(store), sessions, prefs, zone)
    placements, unplaced = place_activities(waiting, slots, sessions, prefs, zone)
    free = sum(int((end - start).total_seconds() // 60) for start, end in slots)
    return {"plan_id": plan_id, "placements": placements, "unplaced": unplaced,
            "needed_minutes": sum(a["minutes"] for a in waiting), "free_minutes": free, "past_deadline": due <= now}


def accept_times(store, plan_id, sessions, *, now=None):
    """Save proposed times after re-checking each one against the deadline, classes and other sessions."""
    now = now or _now()
    plan = _plan_or_404(store, plan_id)
    zone = _zone(_course_info(store, plan["course_id"])["time_zone"])
    due = _parse_instant(plan["due_at"], "Due date")
    if not isinstance(sessions, list) or not sessions or len(sessions) > MAX_ACTIVITIES:
        raise PlanError("There are no times to save.")
    meetings = _confirmed_meetings(store)
    accepted = []
    for session in sessions:
        activity = store.get_plan_activity(session.get("activity_id"))
        if not activity or activity["plan_id"] != plan_id:
            raise LookupError("Step not found.")
        if activity["done"]:
            raise PlanError("A finished step cannot be scheduled.")
        start, end = _parse_instant(session.get("start_at"), "Start"), _parse_instant(session.get("end_at"), "End")
        if end - start != timedelta(minutes=activity["minutes"]):
            raise PlanError("A session must be exactly as long as its step.")
        if start < now - timedelta(minutes=1):
            raise PlanError("A session cannot start in the past.")
        if end > due:
            raise PlanError("A session must finish before the deadline.")
        if start.astimezone(zone).date() != end.astimezone(zone).date():
            raise PlanError("A session must stay within one day.")
        accepted.append((activity, start, end))
    replaced = {a["activity_id"] for a, _, _ in accepted}
    others = [(_parse_instant(a["start_at"], "Start"), _parse_instant(a["end_at"], "End"))
              for a in store.list_plan_activities() if a["start_at"] and not a["done"] and a["activity_id"] not in replaced]
    taken = list(others)
    for activity, start, end in accepted:
        fixed = _meeting_spans(meetings, start.astimezone(zone).date(), zone)
        if any(start < b and a < end for a, b in fixed + taken):
            raise PlanError("A proposed time now overlaps a class or another study session. Ask for new times.")
        taken.append((start, end))
    stamp = _iso(now)
    for activity, start, end in accepted:
        store.save_plan_activity({**activity, "start_at": _iso(start), "end_at": _iso(end), "updated_at": stamp})
    return [store.get_plan_activity(activity["activity_id"]) for activity, _, _ in accepted]


# ---------------------------------------------------------------- choosing a time by hand
def _clock_text(moment, zone):
    local = moment.astimezone(zone)
    return f"{local.hour % 12 or 12}:{local.minute:02d} {'AM' if local.hour < 12 else 'PM'}"


def _range_text(start, end, zone):
    first, last = _clock_text(start, zone), _clock_text(end, zone)
    return f"{first[:-3]} \u2013 {last}" if first[-2:] == last[-2:] else f"{first} \u2013 {last}"


def _wall_text(clock):
    hours, minutes = divmod(_minutes_of(clock), 60)
    return f"{hours % 12 or 12}:{minutes:02d} {'AM' if hours < 12 else 'PM'}"


def find_conflicts(store, start, end, zone, *, skip_activity):
    """Everything the student already has that overlaps (start, end): classes, study blocks, and other plans' sessions."""
    day = start.astimezone(zone).date()
    found = []
    for meeting in _confirmed_meetings(store):
        if DAYS[day.weekday()] not in meeting["days"]:
            continue
        begin, finish = _at(day, _minutes_of(meeting["start_time"]), zone), _at(day, _minutes_of(meeting["end_time"]), zone)
        if start < finish and begin < end:
            try:
                course = _course_info(store, meeting["course_id"])["course_code"] or "A class"
            except LookupError:
                course = "A class"
            study = meeting["kind"] == "study"
            found.append({"kind": "study_block" if study else "class",
                          "title": f"{course} study block" if study else f"{course} {meeting['kind']}",
                          "when": f"{_wall_text(meeting['start_time'])} \u2013 {_wall_text(meeting['end_time'])}"})
    plans = {plan["plan_id"]: plan for plan in store.list_study_plans()}
    for activity in store.list_plan_activities():
        if activity["activity_id"] == skip_activity or activity["done"] or not activity["start_at"]:
            continue
        begin, finish = _parse_instant(activity["start_at"], "Start"), _parse_instant(activity["end_at"], "End")
        if start < finish and begin < end:
            owner = plans.get(activity["plan_id"], {}).get("title", "a plan")
            found.append({"kind": "session", "title": f"{activity['title']} ({owner})", "when": _range_text(begin, finish, zone),
                          "activity_id": activity["activity_id"]})
    return found


def set_time(store, plan_id, activity_id, start_at, *, replace=False, now=None):
    """Let the student choose when to study a step.

    A time that overlaps something already booked is refused with the list of what it overlaps (PlanConflict) unless the student
    chose to add it anyway (`replace`). Adding anyway keeps classes and study blocks where they are (they are weekly commitments, so
    both show) and replaces any other study session at that time: that session loses its time and shows as not scheduled.
    """
    now = now or _now()
    plan = _plan_or_404(store, plan_id)
    activity = store.get_plan_activity(activity_id)
    if not activity or activity["plan_id"] != plan_id:
        raise LookupError("Step not found.")
    if activity["done"]:
        raise PlanError("A finished step cannot be scheduled.")
    zone = _zone(_course_info(store, plan["course_id"])["time_zone"])
    start = _parse_instant(start_at, "Start")
    end = start + timedelta(minutes=activity["minutes"])
    if start < now - timedelta(minutes=1):
        raise PlanError("That time has already passed.")
    if end > _parse_instant(plan["due_at"], "Due date"):
        raise PlanError("A session must finish before the deadline.")
    if start.astimezone(zone).date() != end.astimezone(zone).date():
        raise PlanError("A session must stay within one day. Choose an earlier start or a shorter step.")
    conflicts = find_conflicts(store, start, end, zone, skip_activity=activity_id)
    if conflicts and not replace:
        raise PlanConflict("You already have something at this time.", conflicts)
    stamp = _iso(now)
    replaced = []
    for item in conflicts:
        if item["kind"] == "session":
            other = store.get_plan_activity(item["activity_id"])
            store.save_plan_activity({**other, "start_at": None, "end_at": None, "updated_at": stamp})
            replaced.append({"activity_id": other["activity_id"], "title": item["title"]})
    saved = {**activity, "start_at": _iso(start), "end_at": _iso(end), "updated_at": stamp}
    store.save_plan_activity(saved)
    return {"activity": saved, "replaced": replaced, "overlaps": [c for c in conflicts if c["kind"] != "session"]}


# ---------------------------------------------------------------- reading
def build_plans(store):
    """Every plan with its steps and simple totals, from saved data only."""
    by_plan = defaultdict(list)
    for activity in store.list_plan_activities():
        by_plan[activity["plan_id"]].append(activity)
    plans = []
    for plan in store.list_study_plans():
        steps = by_plan.get(plan["plan_id"], [])
        try:
            info = _course_info(store, plan["course_id"])
        except LookupError:
            info = {"course_code": None, "course_name": None, "time_zone": None}
        plans.append({**plan, **info, "activities": steps,
                      "needed_minutes": sum(a["minutes"] for a in steps if not a["done"]),
                      "scheduled_minutes": sum(a["minutes"] for a in steps if not a["done"] and a["start_at"]),
                      "done_minutes": sum(a["minutes"] for a in steps if a["done"])})
    return {"plans": plans, "preferences": get_preferences(store)}
