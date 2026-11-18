"""Date phrases students actually use, resolved deterministically in the course's time zone."""
import time
import unittest
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from backend.app.structured_canvas_service import date_window, due_in_window, DATE_PHRASE_HELP

CHI = "America/Chicago"
ZONE = ZoneInfo(CHI)
# Tuesday 2026-11-17 12:00 in Chicago.
TUE_NOON = datetime(2026, 11, 17, 18, 0, tzinfo=timezone.utc)


def local(moment):
    return moment.astimezone(ZONE).strftime("%Y-%m-%d %H:%M")


def window(phrase, now=TUE_NOON, zone=CHI):
    start, end = date_window(phrase, now, zone)
    return local(start), local(end)


class SupportedPhraseTests(unittest.TestCase):
    def test_single_days(self):
        cases = {
            "today": ("2026-11-17 00:00", "2026-11-18 00:00"), "tomorrow": ("2026-11-18 00:00", "2026-11-19 00:00"),
            "day after tomorrow": ("2026-11-19 00:00", "2026-11-20 00:00"), "the day after tomorrow": ("2026-11-19 00:00", "2026-11-20 00:00"),
            "yesterday": ("2026-11-16 00:00", "2026-11-17 00:00"),
            "friday": ("2026-11-20 00:00", "2026-11-21 00:00"), "this friday": ("2026-11-20 00:00", "2026-11-21 00:00"),
            "Fri": ("2026-11-20 00:00", "2026-11-21 00:00"), "thurs": ("2026-11-19 00:00", "2026-11-20 00:00"),
            "tuesday": ("2026-11-17 00:00", "2026-11-18 00:00"),                      # today is a Tuesday: today
            "monday": ("2026-11-23 00:00", "2026-11-24 00:00"),                      # next Monday, not the one that passed
            "next friday": ("2026-11-27 00:00", "2026-11-28 00:00"), "next monday": ("2026-11-23 00:00", "2026-11-24 00:00"),
            "in 3 days": ("2026-11-20 00:00", "2026-11-21 00:00"), "in two weeks": ("2026-12-01 00:00", "2026-12-02 00:00"),
            "in a week": ("2026-11-24 00:00", "2026-11-25 00:00"),
        }
        for phrase, expected in cases.items():
            self.assertEqual(window(phrase), expected, phrase)

    def test_ranges(self):
        cases = {
            "this week": ("2026-11-16 00:00", "2026-11-23 00:00"), "next week": ("2026-11-23 00:00", "2026-11-30 00:00"),
            "last week": ("2026-11-09 00:00", "2026-11-16 00:00"),
            "this weekend": ("2026-11-21 00:00", "2026-11-23 00:00"), "next weekend": ("2026-11-28 00:00", "2026-11-30 00:00"),
            "this month": ("2026-11-01 00:00", "2026-12-01 00:00"), "next month": ("2026-12-01 00:00", "2026-01-01 00:00".replace("2026-01", "2027-01")),
            "rest of the week": ("2026-11-17 00:00", "2026-11-23 00:00"), "end of the week": ("2026-11-17 00:00", "2026-11-23 00:00"),
        }
        for phrase, expected in cases.items():
            self.assertEqual(window(phrase), expected, phrase)

    def test_by_means_everything_up_to_and_including_that_day(self):
        self.assertEqual(window("by friday"), ("2026-11-17 00:00", "2026-11-21 00:00"))
        self.assertEqual(window("by tomorrow"), ("2026-11-17 00:00", "2026-11-19 00:00"))
        self.assertEqual(window("until friday"), ("2026-11-17 00:00", "2026-11-21 00:00"))
        self.assertEqual(window("through friday"), ("2026-11-17 00:00", "2026-11-21 00:00"))
        self.assertEqual(window("no later than friday"), ("2026-11-17 00:00", "2026-11-21 00:00"))
        self.assertEqual(window("by next week"), ("2026-11-17 00:00", "2026-11-30 00:00"))
        self.assertEqual(window("by the end of the week"), ("2026-11-17 00:00", "2026-11-23 00:00"))
        self.assertEqual(window("by nov 25"), ("2026-11-17 00:00", "2026-11-26 00:00"))

    def test_before_excludes_that_day(self):
        self.assertEqual(window("before friday"), ("2026-11-17 00:00", "2026-11-20 00:00"))
        self.assertEqual(window("before tomorrow"), ("2026-11-17 00:00", "2026-11-18 00:00"))

    def test_upcoming_means_the_next_two_weeks_from_now(self):
        for phrase in ("upcoming", "soon", "coming up", "due soon"):
            start, end = date_window(phrase, TUE_NOON, CHI)
            self.assertEqual((local(start), local(end)), ("2026-11-17 12:00", "2026-12-01 12:00"), phrase)

    def test_tonight_is_the_rest_of_today(self):
        self.assertEqual(window("tonight"), ("2026-11-17 12:00", "2026-11-18 00:00"))

    def test_calendar_dates(self):
        iso = ("2026-11-20 00:00", "2026-11-21 00:00")
        for phrase in ("2026-11-20", "11/20", "11/20/2026", "11/20/26", "nov 20", "Nov. 20th", "November 20", "20 nov", "20th of november",
                       "the 20th", "20th", "nov 20 2026", "on November 20th"):
            self.assertEqual(window(phrase), iso, phrase)

    def test_punctuation_case_spacing_and_filler_are_ignored(self):
        expected = window("friday")
        for phrase in ("Friday?", "  FRIDAY  ", "due Friday", "due on Friday", "on the friday", "for friday!", "which is friday"):
            self.assertEqual(window(phrase), expected, phrase)
        self.assertEqual(window("BY  FRIDAY!!"), window("by friday"))
        self.assertEqual(window("due by friday"), window("by friday"))


class BoundaryTests(unittest.TestCase):
    def test_just_before_midnight_uses_the_course_day_not_utc(self):
        late = datetime(2026, 11, 18, 5, 30, tzinfo=timezone.utc)            # 11:30 PM Tuesday in Chicago, already Wednesday in UTC
        self.assertEqual(window("tomorrow", late), ("2026-11-18 00:00", "2026-11-19 00:00"))
        self.assertEqual(window("today", late), ("2026-11-17 00:00", "2026-11-18 00:00"))
        self.assertEqual(window("tonight", late), ("2026-11-17 23:30", "2026-11-18 00:00"))

    def test_a_deadline_at_1159_pm_belongs_to_that_local_day(self):
        start, end = date_window("friday", TUE_NOON, CHI)
        self.assertTrue(due_in_window({"due_at": "2026-11-21T05:59:00Z"}, (start, end)))    # Fri 11:59 PM CST
        self.assertFalse(due_in_window({"due_at": "2026-11-21T06:00:00Z"}, (start, end)))   # Sat 12:00 AM CST
        self.assertTrue(due_in_window({"due_at": "2026-11-21T05:59:00Z"}, date_window("by friday", TUE_NOON, CHI)))

    def test_weekend_when_it_is_already_the_weekend(self):
        saturday = datetime(2026, 11, 21, 18, 0, tzinfo=timezone.utc)
        sunday = datetime(2026, 11, 22, 18, 0, tzinfo=timezone.utc)
        self.assertEqual(window("this weekend", saturday), ("2026-11-21 00:00", "2026-11-23 00:00"))
        self.assertEqual(window("this weekend", sunday), ("2026-11-21 00:00", "2026-11-23 00:00"))    # still the current weekend
        self.assertEqual(window("next weekend", sunday), ("2026-11-28 00:00", "2026-11-30 00:00"))

    def test_weekday_phrases_on_a_weekend_and_on_the_target_day(self):
        saturday = datetime(2026, 11, 21, 18, 0, tzinfo=timezone.utc)
        self.assertEqual(window("friday", saturday), ("2026-11-27 00:00", "2026-11-28 00:00"))   # the next Friday
        self.assertEqual(window("next friday", saturday), ("2026-11-27 00:00", "2026-11-28 00:00"))
        self.assertEqual(window("by friday", saturday), ("2026-11-21 00:00", "2026-11-28 00:00"))

    def test_year_rollover(self):
        late_december = datetime(2026, 12, 28, 18, 0, tzinfo=timezone.utc)
        self.assertEqual(window("jan 5", late_december), ("2027-01-05 00:00", "2027-01-06 00:00"))     # the next one, not 2026
        self.assertEqual(window("the 3rd", late_december), ("2027-01-03 00:00", "2027-01-04 00:00"))
        self.assertEqual(window("next month", late_december), ("2027-01-01 00:00", "2027-02-01 00:00"))
        self.assertEqual(window("dec 20", late_december), ("2026-12-20 00:00", "2026-12-21 00:00"))     # a recent past date stays this year
        self.assertEqual(window("in 10 days", late_december), ("2027-01-07 00:00", "2027-01-08 00:00"))

    def test_daylight_saving_days_have_23_and_25_hours(self):
        before_fall_back = datetime(2026, 10, 31, 18, 0, tzinfo=timezone.utc)      # DST ends Sunday 2026-11-01 in Chicago
        start, end = date_window("tomorrow", before_fall_back, CHI)
        hours = lambda a, b: (b.astimezone(timezone.utc) - a.astimezone(timezone.utc)).total_seconds() / 3600   # same-zone subtraction ignores DST
        self.assertEqual(hours(start, end), 25)
        before_spring = datetime(2026, 3, 7, 18, 0, tzinfo=timezone.utc)           # DST starts Sunday 2026-03-08
        start, end = date_window("tomorrow", before_spring, CHI)
        self.assertEqual(hours(start, end), 23)

    def test_other_time_zones_shift_the_day(self):
        self.assertEqual(window("today", TUE_NOON, "Asia/Tokyo"), ("2026-11-17 09:00", "2026-11-18 09:00"))   # Tokyo's Wed is 9am-based in Chicago clock
        start, _ = date_window("today", TUE_NOON, "Asia/Tokyo")
        self.assertEqual(start.astimezone(ZoneInfo("Asia/Tokyo")).strftime("%Y-%m-%d"), "2026-11-18")        # it is already Wednesday there


class RejectionTests(unittest.TestCase):
    def test_unclear_or_invalid_phrases_ask_for_clarification_instead_of_guessing(self):
        for phrase in ("", "   ", "banana", "by banana", "someday", "sometime next week maybe", "in 0 days", "in 500 days",
                       "in -2 days", "feb 30", "13/45", "2026-13-40", "nov 32", "the 45th", "by", "next", "next banana",
                       "friday friday", "this tomorrow"):
            with self.assertRaises(ValueError, msg=repr(phrase)) as caught:
                date_window(phrase, TUE_NOON, CHI)
            self.assertEqual(str(caught.exception), DATE_PHRASE_HELP)

    def test_non_text_and_oversized_input_are_rejected(self):
        for value in (None, 5, ["friday"], {"a": 1}, "x" * 81):
            with self.assertRaises(ValueError):
                date_window(value, TUE_NOON, CHI)

    def test_pathological_input_is_fast(self):
        started = time.perf_counter()
        for phrase in ("by " * 26 + "friday", "1" * 79, "in " + "9" * 77, "the " * 19, "nov " + "1" * 70, "/" * 80):
            try:
                date_window(phrase, TUE_NOON, CHI)
            except ValueError:
                pass
        self.assertLess(time.perf_counter() - started, 0.5)

    def test_the_help_text_lists_what_is_supported(self):
        for word in ("tonight", "tomorrow", "Friday", "this weekend", "in 3 days", "by"):
            self.assertIn(word, DATE_PHRASE_HELP)


if __name__ == "__main__":
    unittest.main()


import backend.tests.ai_consent_granted  # noqa: E402,F401  (AI tests use fake providers)
