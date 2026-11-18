"""Study plans (Stage A): free-time rules, first-fit placement, plan validation, and the API. Offline; no AI."""
import asyncio
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

from backend.app import main, schedule_service, study_plan_service as sp
from backend.app.ingestion_store import IngestionStore
from backend.tests.test_retention_api import request

CHI = ZoneInfo("America/Chicago")
PREFS = dict(sp.DEFAULT_PREFERENCES)                                   # 09:00-21:00, 240 min/day, 120 min buffer
NOW = datetime(2026, 11, 17, 18, 0, tzinfo=timezone.utc)               # Tuesday 12:00 in Chicago
DUE = datetime(2026, 11, 21, 5, 59, tzinfo=timezone.utc)               # Friday 23:59 in Chicago


def local(moment):
    return moment.astimezone(CHI)


def meeting(days, start, end, kind="lecture"):
    return {"days": days, "start_time": start, "end_time": end, "kind": kind}


def hhmm(moment):
    return local(moment).strftime("%a %H:%M")


class FreeSlotTests(unittest.TestCase):
    def slots(self, meetings=(), sessions=(), prefs=PREFS, now=NOW, due=DUE):
        return sp.free_slots(now, due, list(meetings), list(sessions), prefs, CHI)

    def test_study_hours_each_day_from_now_until_the_buffer(self):
        got = [(hhmm(a), hhmm(b)) for a, b in self.slots()]
        self.assertEqual(got, [("Tue 12:00", "Tue 21:00"), ("Wed 09:00", "Wed 21:00"), ("Thu 09:00", "Thu 21:00"), ("Fri 09:00", "Fri 21:00")])

    def test_classes_and_saved_study_blocks_are_never_offered(self):
        got = [(hhmm(a), hhmm(b)) for a, b in self.slots([meeting(["TUE"], "14:00", "15:20"), meeting(["TUE"], "18:00", "19:00", "study")])]
        self.assertEqual(got[:3], [("Tue 12:00", "Tue 14:00"), ("Tue 15:20", "Tue 18:00"), ("Tue 19:00", "Tue 21:00")])

    def test_saved_sessions_of_any_plan_are_busy(self):
        session = (datetime(2026, 11, 18, 15, 0, tzinfo=timezone.utc), datetime(2026, 11, 18, 17, 0, tzinfo=timezone.utc))   # Wed 09:00-11:00
        self.assertEqual(hhmm(self.slots(sessions=[session])[1][0]), "Wed 11:00")

    def test_a_moment_inside_a_minute_rounds_up_to_five_minutes(self):
        start = self.slots(now=NOW + timedelta(minutes=3, seconds=10))[0][0]
        self.assertEqual(hhmm(start), "Tue 12:05")

    def test_the_buffer_before_the_deadline_is_kept_free(self):
        due = datetime(2026, 11, 20, 18, 0, tzinfo=timezone.utc)       # Fri 12:00 local, so 10:00 is the last allowed moment
        self.assertEqual((hhmm(self.slots(due=due)[-1][0]), hhmm(self.slots(due=due)[-1][1])), ("Fri 09:00", "Fri 10:00"))

    def test_no_time_when_the_deadline_is_inside_the_buffer(self):
        self.assertEqual(self.slots(due=NOW + timedelta(minutes=90)), [])
        self.assertEqual(self.slots(due=NOW - timedelta(hours=1)), [])

    def test_gaps_shorter_than_half_an_hour_are_dropped(self):
        got = self.slots([meeting(["TUE"], "12:15", "20:45")])
        self.assertEqual(hhmm(got[0][0]), "Wed 09:00")                  # 12:00-12:15 and 20:45-21:00 are too short

    def test_wall_clock_hours_survive_the_end_of_daylight_saving_time(self):
        now = datetime(2026, 10, 31, 0, 0, tzinfo=timezone.utc)
        due = datetime(2026, 11, 3, 12, 0, tzinfo=timezone.utc)
        starts = [a.astimezone(timezone.utc).hour for a, _ in self.slots(now=now, due=due) if local(a).strftime("%H:%M") == "09:00"]
        self.assertEqual(starts[:3], [14, 15, 15])                      # Oct 31 CDT (UTC-5), then Nov 1 and Nov 2 CST (UTC-6)


class PlacementTests(unittest.TestCase):
    def place(self, activities, slots, sessions=(), prefs=PREFS):
        return sp.place_activities(activities, slots, list(sessions), prefs, CHI)

    def slots(self):
        return sp.free_slots(NOW, DUE, [], [], PREFS, CHI)

    def test_first_fit_in_order_with_a_short_break(self):
        placed, unplaced = self.place([{"activity_id": "a", "minutes": 60}, {"activity_id": "b", "minutes": 30}], self.slots())
        self.assertEqual(unplaced, [])
        spans = [(hhmm(sp._parse_instant(p["start_at"], "s")), hhmm(sp._parse_instant(p["end_at"], "e"))) for p in placed]
        self.assertEqual(spans, [("Tue 12:00", "Tue 13:00"), ("Tue 13:10", "Tue 13:40")])

    def test_the_daily_limit_moves_work_to_the_next_day(self):
        prefs = {**PREFS, "max_minutes_per_day": 90}
        placed, _ = self.place([{"activity_id": "a", "minutes": 60}, {"activity_id": "b", "minutes": 60}], self.slots(), prefs=prefs)
        self.assertEqual([local(sp._parse_instant(p["start_at"], "s")).strftime("%a") for p in placed], ["Tue", "Wed"])

    def test_minutes_already_saved_that_day_count_toward_the_limit(self):
        busy = (datetime(2026, 11, 17, 19, 0, tzinfo=timezone.utc), datetime(2026, 11, 17, 21, 0, tzinfo=timezone.utc))   # 2 h on Tuesday
        prefs = {**PREFS, "max_minutes_per_day": 150}
        placed, _ = self.place([{"activity_id": "a", "minutes": 60}], sp.free_slots(NOW, DUE, [], [busy], prefs, CHI), [busy], prefs)
        self.assertEqual(local(sp._parse_instant(placed[0]["start_at"], "s")).strftime("%a"), "Wed")

    def test_a_step_with_no_room_is_reported_instead_of_squeezed_in(self):
        busy_all = [meeting([d], "09:00", "20:00") for d in ("TUE", "WED", "THU", "FRI")]
        slots = sp.free_slots(NOW, DUE, busy_all, [], PREFS, CHI)
        placed, unplaced = self.place([{"activity_id": "long", "minutes": 120}], slots)
        self.assertEqual(placed, [])
        self.assertIn("No free gap of 120 minutes", unplaced[0]["reason"])

    def test_the_limit_is_named_when_gaps_exist_but_the_day_is_full(self):
        prefs = {**PREFS, "max_minutes_per_day": 30}
        _, unplaced = self.place([{"activity_id": "a", "minutes": 60}], self.slots(), prefs=prefs)
        self.assertIn("daily study limit", unplaced[0]["reason"])


class PlanFixture(unittest.TestCase):
    """Shared setup: a temp store with two current courses and a fake Canvas assignment lookup."""
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.store = IngestionStore("student", Path(self.directory.name) / "t.sqlite3")
        self.store.save_course({"course_id": 1, "synced_at": "x", "course": {"metadata": {"course_code": "CSE 3302", "name": "PL", "time_zone": "America/Chicago"}}})
        self.store.save_course({"course_id": 2, "synced_at": "x", "course": {"metadata": {"course_code": "CSE 3311", "name": "SE", "time_zone": "America/Chicago"}}})
        self.store.save_sync_state({"courses": [{"course_id": 1}, {"course_id": 2}], "status": "completed"})
        self.assignment = {"course_id": 1, "id": 7, "name": "Homework 7", "due_at": DUE.isoformat(), "submission_status": "incomplete"}

    def fetch(self, course_id, assignment_id):
        return self.assignment if (course_id, assignment_id) == (1, 7) else None

    def plan(self, **over):
        data = {"course_id": 1, "assignment_id": 7, **over}
        return sp.create_plan(self.store, data, fetch_assignment=self.fetch, now=NOW)

    def step(self, plan, title="Read", minutes=60):
        return sp.add_activity(self.store, plan["plan_id"], {"title": title, "minutes": minutes}, now=NOW)


class PlanTests(PlanFixture):

    # ---- creating plans
    def test_an_assignment_plan_takes_its_title_and_deadline_from_canvas_not_the_client(self):
        plan = self.plan(title="Hacked title", due_at="2030-01-01T00:00:00Z")
        self.assertEqual((plan["title"], plan["kind"], plan["assignment_id"]), ("Homework 7", "assignment", 7))
        self.assertEqual(sp._parse_instant(plan["due_at"], "d"), DUE)

    def test_completed_undated_missing_and_past_assignments_cannot_be_planned(self):
        for status in ("submitted", "graded", "excused"):
            self.assignment = {**self.assignment, "submission_status": status}
            with self.assertRaisesRegex(sp.PlanError, "already complete"):
                self.plan()
        self.assignment = {**self.assignment, "submission_status": "incomplete", "due_at": None}
        with self.assertRaisesRegex(sp.PlanError, "no due date"):
            self.plan()
        self.assignment = {**self.assignment, "due_at": (NOW - timedelta(hours=1)).isoformat()}
        with self.assertRaisesRegex(sp.PlanError, "already passed"):
            self.plan()
        with self.assertRaises(LookupError):
            self.plan(assignment_id=99)
        with self.assertRaises(LookupError):
            self.plan(course_id=404)

    def test_a_custom_item_needs_a_title_and_a_future_date_with_a_zone(self):
        ok = sp.create_plan(self.store, {"course_id": 2, "title": "  Midterm   exam ", "due_at": "2026-12-01T15:00:00Z"}, fetch_assignment=self.fetch, now=NOW)
        self.assertEqual((ok["title"], ok["kind"], ok["assignment_id"]), ("Midterm exam", "custom", None))
        for bad in ({"title": "", "due_at": "2026-12-01T15:00:00Z"}, {"title": "x" * 121, "due_at": "2026-12-01T15:00:00Z"},
                    {"title": "Exam", "due_at": "2026-12-01T15:00:00"}, {"title": "Exam", "due_at": "soon"},
                    {"title": "Exam", "due_at": "2026-11-01T15:00:00Z"}, {"title": "Exam"}):
            with self.assertRaises(sp.PlanError, msg=str(bad)):
                sp.create_plan(self.store, {"course_id": 2, **bad}, fetch_assignment=self.fetch, now=NOW)

    # ---- steps
    def test_steps_are_validated_ordered_and_capped(self):
        plan = self.plan()
        for bad in ({"title": "", "minutes": 30}, {"title": "x", "minutes": 5}, {"title": "x", "minutes": 241}, {"title": "x", "minutes": True}, {"title": "x", "minutes": "30"}):
            with self.assertRaises(sp.PlanError, msg=str(bad)):
                sp.add_activity(self.store, plan["plan_id"], bad)
        first, second = self.step(plan, "One"), self.step(plan, "Two")
        self.assertEqual((first["position"], second["position"]), (0, 1))
        for i in range(sp.MAX_ACTIVITIES - 2):
            self.step(plan, f"s{i}", 10)
        with self.assertRaisesRegex(sp.PlanError, "at most"):
            self.step(plan)
        with self.assertRaises(LookupError):
            sp.add_activity(self.store, "0" * 32, {"title": "x", "minutes": 30})

    def test_changing_a_steps_length_clears_its_saved_time_and_done_is_strict(self):
        plan = self.plan(); step = self.step(plan)
        sp.accept_times(self.store, plan["plan_id"], [{"activity_id": step["activity_id"], "start_at": "2026-11-18T15:00:00Z", "end_at": "2026-11-18T16:00:00Z"}], now=NOW)
        changed = sp.update_activity(self.store, plan["plan_id"], step["activity_id"], {"minutes": 90})
        self.assertEqual((changed["minutes"], changed["start_at"]), (90, None))
        self.assertTrue(sp.update_activity(self.store, plan["plan_id"], step["activity_id"], {"done": True})["done"])
        with self.assertRaises(sp.PlanError):
            sp.update_activity(self.store, plan["plan_id"], step["activity_id"], {"done": "yes"})
        other = self.plan(course_id=1)
        with self.assertRaises(LookupError):
            sp.update_activity(self.store, other["plan_id"], step["activity_id"], {"done": False})    # a step belongs to one plan

    # ---- proposing and accepting
    def test_suggest_saves_nothing_and_accept_saves_what_was_proposed(self):
        plan = self.plan(); a, b = self.step(plan, "A", 60), self.step(plan, "B", 45)
        proposal = sp.suggest_times(self.store, plan["plan_id"], now=NOW)
        self.assertEqual((len(proposal["placements"]), proposal["unplaced"], proposal["needed_minutes"]), (2, [], 105))
        self.assertTrue(all(x["start_at"] is None for x in self.store.list_plan_activities()))
        saved = sp.accept_times(self.store, plan["plan_id"], proposal["placements"], now=NOW)
        self.assertEqual([s["activity_id"] for s in saved], [a["activity_id"], b["activity_id"]])
        self.assertTrue(all(x["start_at"] for x in self.store.list_plan_activities()))
        again = sp.suggest_times(self.store, plan["plan_id"], now=NOW)                 # nothing left to place
        self.assertEqual((again["placements"], again["needed_minutes"]), ([], 0))

    def test_a_second_plan_is_placed_around_the_first(self):
        first = self.plan(); self.step(first, "A", 120)
        sp.accept_times(self.store, first["plan_id"], sp.suggest_times(self.store, first["plan_id"], now=NOW)["placements"], now=NOW)
        second = sp.create_plan(self.store, {"course_id": 2, "title": "Exam", "due_at": DUE.isoformat()}, fetch_assignment=self.fetch, now=NOW)
        self.step(second, "B", 60)
        mine = sp.suggest_times(self.store, second["plan_id"], now=NOW)["placements"][0]
        taken = self.store.list_plan_activities(first["plan_id"])[0]
        self.assertGreaterEqual(sp._parse_instant(mine["start_at"], "s"), sp._parse_instant(taken["end_at"], "e"))

    def test_class_meetings_are_respected_when_proposing(self):
        schedule_service.create_meeting(self.store, 1, {"days": ["TUE"], "start_time": "12:00", "end_time": "20:00"})
        plan = self.plan(); self.step(plan, "A", 60)
        start = sp.suggest_times(self.store, plan["plan_id"], now=NOW)["placements"][0]["start_at"]
        self.assertEqual(hhmm(sp._parse_instant(start, "s")), "Tue 20:00")

    def test_not_enough_time_is_reported_honestly(self):
        plan = sp.create_plan(self.store, {"course_id": 2, "title": "Soon", "due_at": (NOW + timedelta(hours=3)).isoformat()}, fetch_assignment=self.fetch, now=NOW)
        self.step(plan, "Big", 120)
        result = sp.suggest_times(self.store, plan["plan_id"], now=NOW)
        self.assertEqual((result["placements"], len(result["unplaced"]), result["free_minutes"]), ([], 1, 60))

    def test_accepting_rechecks_every_rule_on_the_server(self):
        plan = self.plan(); step = self.step(plan, "A", 60); sid = step["activity_id"]
        schedule_service.create_meeting(self.store, 1, {"days": ["WED"], "start_time": "10:00", "end_time": "11:00"})
        def attempt(start, end, **kw):
            return lambda: sp.accept_times(self.store, plan["plan_id"], [{"activity_id": sid, "start_at": start, "end_at": end}], now=kw.get("now", NOW))
        cases = {
            "wrong length": attempt("2026-11-18T15:00:00Z", "2026-11-18T15:30:00Z"),
            "in the past": attempt("2026-11-17T10:00:00Z", "2026-11-17T11:00:00Z"),
            "after the deadline": attempt("2026-11-21T05:30:00Z", "2026-11-21T06:30:00Z"),
            "over a class": attempt("2026-11-18T16:30:00Z", "2026-11-18T17:30:00Z"),            # Wed 10:30-11:30 local
            "no zone": attempt("2026-11-18T15:00:00", "2026-11-18T16:00:00"),
        }
        for label, run in cases.items():
            with self.assertRaises(sp.PlanError, msg=label):
                run()
        self.assertTrue(all(x["start_at"] is None for x in self.store.list_plan_activities()))      # a refusal saves nothing
        with self.assertRaises(sp.PlanError):
            sp.accept_times(self.store, plan["plan_id"], [], now=NOW)
        self.assertEqual(attempt("2026-11-18T20:00:00Z", "2026-11-18T21:00:00Z")()[0]["start_at"], "2026-11-18T20:00:00+00:00")

    def test_two_steps_cannot_be_saved_over_each_other_and_finished_steps_cannot_be_scheduled(self):
        plan = self.plan(); a, b = self.step(plan, "A", 60), self.step(plan, "B", 60)
        with self.assertRaisesRegex(sp.PlanError, "overlaps"):
            sp.accept_times(self.store, plan["plan_id"], [
                {"activity_id": a["activity_id"], "start_at": "2026-11-18T20:00:00Z", "end_at": "2026-11-18T21:00:00Z"},
                {"activity_id": b["activity_id"], "start_at": "2026-11-18T20:30:00Z", "end_at": "2026-11-18T21:30:00Z"}], now=NOW)
        sp.update_activity(self.store, plan["plan_id"], b["activity_id"], {"done": True})
        with self.assertRaisesRegex(sp.PlanError, "finished"):
            sp.accept_times(self.store, plan["plan_id"], [{"activity_id": b["activity_id"], "start_at": "2026-11-18T20:00:00Z", "end_at": "2026-11-18T21:00:00Z"}], now=NOW)

    def test_clearing_a_time_and_deleting_cleans_up(self):
        plan = self.plan(); step = self.step(plan)
        sp.accept_times(self.store, plan["plan_id"], [{"activity_id": step["activity_id"], "start_at": "2026-11-18T20:00:00Z", "end_at": "2026-11-18T21:00:00Z"}], now=NOW)
        sp.clear_time(self.store, plan["plan_id"], step["activity_id"])
        self.assertIsNone(self.store.get_plan_activity(step["activity_id"])["start_at"])
        sp.delete_plan(self.store, plan["plan_id"])
        self.assertEqual((self.store.list_study_plans(), self.store.list_plan_activities()), ([], []))
        with self.assertRaises(LookupError):
            sp.delete_plan(self.store, plan["plan_id"])

    def test_plans_report_totals_and_survive_when_a_course_is_no_longer_current(self):
        plan = self.plan(); a, b = self.step(plan, "A", 60), self.step(plan, "B", 30)
        sp.update_activity(self.store, plan["plan_id"], b["activity_id"], {"done": True})
        built = sp.build_plans(self.store)["plans"][0]
        self.assertEqual((built["needed_minutes"], built["done_minutes"], built["course_code"]), (60, 30, "CSE 3302"))
        self.store.save_sync_state({"courses": [{"course_id": 2}], "status": "completed"})
        self.assertIsNone(sp.build_plans(self.store)["plans"][0]["course_code"])

    # ---- preferences
    def test_preferences_are_validated_saved_and_corrupt_values_fall_back(self):
        saved = sp.set_preferences(self.store, {"day_start": "08:00", "max_minutes_per_day": 180})
        self.assertEqual((saved["day_start"], saved["day_end"], saved["max_minutes_per_day"]), ("08:00", "21:00", 180))
        self.assertEqual(sp.get_preferences(IngestionStore("student", Path(self.directory.name) / "t.sqlite3"))["day_start"], "08:00")
        for bad in ({"day_start": "9am"}, {"day_start": "21:00", "day_end": "21:30"}, {"max_minutes_per_day": 10}, {"max_minutes_per_day": True}, {"buffer_minutes": -1}):
            with self.assertRaises(sp.PlanError, msg=str(bad)):
                sp.set_preferences(self.store, bad)
        self.store.update_settings({"study_preferences": "garbage"})
        self.assertEqual(sp.get_preferences(self.store), PREFS)


class ChosenTimeTests(PlanFixture):
    """The student picks the time. A booked time warns; adding anyway replaces other study sessions and keeps classes."""

    def setUp(self):
        super().setUp()
        self.plan_record = self.plan()
        self.pid = self.plan_record["plan_id"]
        self.a = self.step(self.plan_record, "Read", 60)
        self.b = self.step(self.plan_record, "Practice", 60)

    def choose(self, step, start, **kw):
        return sp.set_time(self.store, self.pid, step["activity_id"], start, now=NOW, **kw)

    def test_a_free_time_is_saved_with_the_steps_own_length(self):
        result = self.choose(self.a, "2026-11-18T20:00:00Z")
        self.assertEqual((result["activity"]["start_at"], result["activity"]["end_at"]), ("2026-11-18T20:00:00+00:00", "2026-11-18T21:00:00+00:00"))
        self.assertEqual((result["replaced"], result["overlaps"]), ([], []))

    def test_a_class_at_that_time_warns_and_saves_nothing(self):
        schedule_service.create_meeting(self.store, 1, {"days": ["WED"], "start_time": "14:00", "end_time": "15:20", "kind": "lecture"})
        with self.assertRaises(sp.PlanConflict) as caught:
            self.choose(self.a, "2026-11-18T20:30:00Z")                       # Wed 14:30 local
        self.assertEqual(caught.exception.conflicts, [{"kind": "class", "title": "CSE 3302 lecture", "when": "2:00 PM \u2013 3:20 PM"}])
        self.assertIsNone(self.store.get_plan_activity(self.a["activity_id"])["start_at"])

    def test_adding_anyway_over_a_class_keeps_the_class_and_reports_it(self):
        schedule_service.create_meeting(self.store, 1, {"days": ["WED"], "start_time": "14:00", "end_time": "15:20", "kind": "lecture"})
        result = self.choose(self.a, "2026-11-18T20:30:00Z", replace=True)
        self.assertEqual([c["kind"] for c in result["overlaps"]], ["class"])
        self.assertEqual(result["replaced"], [])
        self.assertEqual(len(self.store.list_class_meetings("confirmed")), 1)            # the class is untouched
        self.assertTrue(self.store.get_plan_activity(self.a["activity_id"])["start_at"])

    def test_a_recurring_study_block_is_named_as_one(self):
        schedule_service.create_meeting(self.store, 1, {"days": ["WED"], "start_time": "14:00", "end_time": "16:00", "kind": "study"})
        with self.assertRaises(sp.PlanConflict) as caught:
            self.choose(self.a, "2026-11-18T20:00:00Z")
        self.assertEqual((caught.exception.conflicts[0]["kind"], caught.exception.conflicts[0]["title"]), ("study_block", "CSE 3302 study block"))

    def test_another_study_session_is_replaced_when_the_student_adds_anyway(self):
        self.choose(self.a, "2026-11-18T20:00:00Z")
        with self.assertRaises(sp.PlanConflict) as caught:
            self.choose(self.b, "2026-11-18T20:30:00Z")
        self.assertEqual(caught.exception.conflicts[0]["kind"], "session")
        self.assertIn("Read (Homework 7)", caught.exception.conflicts[0]["title"])
        self.assertEqual(self.store.get_plan_activity(self.a["activity_id"])["start_at"], "2026-11-18T20:00:00+00:00")   # refused: nothing changed
        result = self.choose(self.b, "2026-11-18T20:30:00Z", replace=True)
        self.assertEqual([r["activity_id"] for r in result["replaced"]], [self.a["activity_id"]])
        self.assertIsNone(self.store.get_plan_activity(self.a["activity_id"])["start_at"])              # the earlier block is replaced
        self.assertEqual(self.store.get_plan_activity(self.b["activity_id"])["start_at"], "2026-11-18T20:30:00+00:00")

    def test_every_overlap_is_listed_together(self):
        schedule_service.create_meeting(self.store, 1, {"days": ["WED"], "start_time": "14:00", "end_time": "15:00", "kind": "lecture"})
        self.choose(self.a, "2026-11-18T20:30:00Z", replace=True)                        # Wed 14:30-15:30, over the class
        with self.assertRaises(sp.PlanConflict) as caught:
            self.choose(self.b, "2026-11-18T20:15:00Z")                                  # overlaps the class and the session
        self.assertEqual(sorted(c["kind"] for c in caught.exception.conflicts), ["class", "session"])

    def test_moving_a_step_over_its_own_old_time_is_not_a_conflict(self):
        self.choose(self.a, "2026-11-18T20:00:00Z")
        moved = self.choose(self.a, "2026-11-18T20:30:00Z")
        self.assertEqual(moved["activity"]["start_at"], "2026-11-18T20:30:00+00:00")
        self.assertEqual(moved["replaced"], [])

    def test_hard_rules_stay_errors_even_when_adding_anyway(self):
        done = self.step(self.plan_record, "Done step", 30)
        sp.update_activity(self.store, self.pid, done["activity_id"], {"done": True})
        cases = {
            "finished step": lambda: self.choose(done, "2026-11-18T20:00:00Z", replace=True),
            "already passed": lambda: self.choose(self.a, "2026-11-17T10:00:00Z", replace=True),
            "after the deadline": lambda: self.choose(self.a, "2026-11-21T05:30:00Z", replace=True),
            "across midnight": lambda: self.choose(self.a, "2026-11-19T05:30:00Z", replace=True),      # Wed 23:30-00:30 local
            "no zone": lambda: self.choose(self.a, "2026-11-18T20:00:00", replace=True),
        }
        for label, run in cases.items():
            with self.assertRaises(sp.PlanError, msg=label):
                run()
        with self.assertRaises(LookupError):
            sp.set_time(self.store, "0" * 32, self.a["activity_id"], "2026-11-18T20:00:00Z", now=NOW)
        self.assertIsNone(self.store.get_plan_activity(self.a["activity_id"])["start_at"])


class PlanApiTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        path = Path(self.directory.name) / "t.sqlite3"
        store = IngestionStore("student", path)
        store.save_course({"course_id": 1, "synced_at": "x", "course": {"metadata": {"course_code": "CSE 3302", "name": "PL", "time_zone": "America/Chicago"}}})
        store.save_sync_state({"courses": [{"course_id": 1}], "status": "completed"})
        patcher = patch.object(main, "default_store", lambda: IngestionStore("student", path))
        patcher.start(); self.addCleanup(patcher.stop)
        self.due = (datetime.now(timezone.utc) + timedelta(days=10)).isoformat()

    def call(self, method, path, payload=None):
        return asyncio.run(request(method, path, payload))

    def test_the_whole_flow_over_http(self):
        code, plan = self.call("POST", "/api/study-plans", {"course_id": 1, "title": "Exam 2", "due_at": self.due})
        self.assertEqual((code, plan["kind"]), (201, "custom"))
        code, step = self.call("POST", f"/api/study-plans/{plan['plan_id']}/activities", {"title": "Review notes", "minutes": 45})
        self.assertEqual(code, 201)
        code, proposal = self.call("POST", f"/api/study-plans/{plan['plan_id']}/suggest")
        self.assertEqual((code, len(proposal["placements"]), proposal["unplaced"]), (200, 1, []))
        code, saved = self.call("PUT", f"/api/study-plans/{plan['plan_id']}/times", {"sessions": proposal["placements"]})
        self.assertEqual((code, len(saved)), (200, 1))
        code, listing = self.call("GET", "/api/study-plans")
        self.assertEqual((code, listing["plans"][0]["scheduled_minutes"], listing["preferences"]["max_minutes_per_day"]), (200, 45, 240))
        code, _ = self.call("PATCH", f"/api/study-plans/{plan['plan_id']}/activities/{step['activity_id']}", {"done": True})
        self.assertEqual(code, 200)
        self.assertEqual(self.call("DELETE", f"/api/study-plans/{plan['plan_id']}/activities/{step['activity_id']}/time")[0], 200)
        self.assertEqual(self.call("DELETE", f"/api/study-plans/{plan['plan_id']}")[0], 200)
        self.assertEqual(self.call("GET", "/api/study-plans")[1]["plans"], [])

    def test_assignment_plans_use_canvas_and_canvas_failures_are_a_safe_503(self):
        item = {"course_id": 1, "id": 7, "name": "Homework 7", "due_at": self.due, "submission_status": "incomplete"}
        with patch.object(main.priority_service, "get_priorities", return_value={"assignments": [item]}):
            code, plan = self.call("POST", "/api/study-plans", {"course_id": 1, "assignment_id": 7, "title": "ignored"})
            self.assertEqual((code, plan["title"]), (201, "Homework 7"))
            self.assertEqual(self.call("POST", "/api/study-plans", {"course_id": 1, "assignment_id": 8})[0], 404)
        import requests
        with patch.object(main.priority_service, "get_priorities", side_effect=requests.ConnectionError("secret host details")):
            code, body = self.call("POST", "/api/study-plans", {"course_id": 1, "assignment_id": 7})
            self.assertEqual(code, 503)
            self.assertNotIn("secret", str(body))

    def test_bad_requests_get_clear_codes(self):
        self.assertEqual(self.call("POST", "/api/study-plans", {"course_id": 1, "title": "x", "due_at": "2001-01-01T00:00:00Z"})[0], 422)
        self.assertEqual(self.call("POST", "/api/study-plans", {"course_id": 99, "title": "x", "due_at": self.due})[0], 404)
        self.assertEqual(self.call("POST", "/api/study-plans", {"course_id": 1, "title": "x", "due_at": self.due, "extra": 1})[0], 422)
        self.assertEqual(self.call("POST", "/api/study-plans", {"course_id": "1", "title": "x", "due_at": self.due})[0], 422)
        self.assertEqual(self.call("POST", "/api/study-plans/not-an-id/suggest")[0], 422)
        self.assertEqual(self.call("POST", f"/api/study-plans/{'0' * 32}/suggest")[0], 404)
        self.assertEqual(self.call("PUT", "/api/study-plans/preferences", {"max_minutes_per_day": 5})[0], 422)
        self.assertEqual(self.call("PUT", "/api/study-plans/preferences", {"max_minutes_per_day": 120})[1]["max_minutes_per_day"], 120)

    def test_choosing_a_time_warns_with_409_then_adds_anyway(self):
        import time as _time
        code, plan = self.call("POST", "/api/study-plans", {"course_id": 1, "title": "Exam", "due_at": self.due})
        code, one = self.call("POST", f"/api/study-plans/{plan['plan_id']}/activities", {"title": "First", "minutes": 60})
        code, two = self.call("POST", f"/api/study-plans/{plan['plan_id']}/activities", {"title": "Second", "minutes": 60})
        start = (datetime.now(timezone.utc) + timedelta(days=3)).replace(minute=0, second=0, microsecond=0)
        if start.astimezone().hour >= 22 or start.astimezone().hour < 7:
            start = start.replace(hour=(start.hour + 12) % 24)
        path = lambda step: f"/api/study-plans/{plan['plan_id']}/activities/{step['activity_id']}/time"
        code, saved = self.call("PUT", path(one), {"start_at": start.isoformat()})
        self.assertEqual(code, 200)
        code, warning = self.call("PUT", path(two), {"start_at": (start + timedelta(minutes=30)).isoformat()})
        self.assertEqual(code, 409)
        self.assertEqual(warning["conflicts"][0]["kind"], "session")
        self.assertIn("something at this time", warning["detail"])
        code, done = self.call("PUT", path(two), {"start_at": (start + timedelta(minutes=30)).isoformat(), "replace_conflicts": True})
        self.assertEqual((code, len(done["replaced"])), (200, 1))
        plans = self.call("GET", "/api/study-plans")[1]["plans"][0]["activities"]
        self.assertEqual([bool(a["start_at"]) for a in plans], [False, True])
        self.assertEqual(self.call("PUT", path(two), {"start_at": start.isoformat(), "replace_conflicts": "yes"})[0], 422)   # strict boolean
        self.assertEqual(self.call("PUT", path(two), {"start_at": "2001-01-01T00:00:00Z"})[0], 422)

    def test_plans_do_not_need_ai_consent(self):
        from backend.app import consent_service
        with patch.object(consent_service, "is_ai_enabled", lambda store: False):
            self.assertEqual(self.call("GET", "/api/study-plans")[0], 200)
            self.assertEqual(self.call("POST", "/api/study-plans", {"course_id": 1, "title": "Exam", "due_at": self.due})[0], 201)


if __name__ == "__main__":
    unittest.main()
