"""Class schedule: the syllabus reader (rules only), confirmed-only trust, and the meeting API."""
import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.app import main, schedule_service as ss
from backend.app.chunking_service import ensure_course_chunks
from backend.app.ingestion_store import IngestionStore
from backend.tests.test_retention_api import request


def one(line, prefixes=("CSE",)):
    return ss.parse_line(line, set(prefixes))


def times_of(line):
    """The parsed time range, or None when the line yields no usable time (including yielding nothing at all)."""
    parsed = one(line)
    return parsed["times"] if parsed else None


class LineParserTests(unittest.TestCase):
    def check(self, line, days, start, end, location=None):
        parsed = one(line)
        self.assertIsNotNone(parsed, line)
        self.assertEqual((parsed["days"], parsed["times"], parsed["location"]), (days, (start, end), location), line)

    def test_phrasings_from_real_syllabi(self):
        self.check("Tuesday and Thursday, 3:30–4:50 p.m.", ["TUE", "THU"], "15:30", "16:50")
        self.check("Tuesday and Thursday, 9:30 am – 10:50 am.", ["TUE", "THU"], "09:30", "10:50")
        self.check("WH  311                               Tu/Th- 2:00 pm to 3:20 pm", ["TUE", "THU"], "14:00", "15:20", "WH 311")
        self.check("Th 5:30PM - 8:20PM\t\tNH 110", ["THU"], "17:30", "20:20", "NH 110")
        self.check("Tue & Thu, 11am to 12.20pm, WH 311", ["TUE", "THU"], "11:00", "12:20", "WH 311")

    def test_other_common_formats(self):
        self.check("MWF 10:00-10:50 AM", ["MON", "WED", "FRI"], "10:00", "10:50")
        self.check("TTh 1:00–2:15 PM", ["TUE", "THU"], "13:00", "14:15")
        self.check("MoWe 3:30-4:45pm", ["MON", "WED"], "15:30", "16:45")
        self.check("M/W/F 9-9:50 am", ["MON", "WED", "FRI"], "09:00", "09:50")
        self.check("T/TH 8:00 - 9:15 a.m.", ["TUE", "THU"], "08:00", "09:15")
        self.check("Class meets on Mondays and Wednesdays from 2 to 3:15 pm in ERB 124", ["MON", "WED"], "14:00", "15:15", "ERB 124")
        self.check("Mon Wed 14:00-15:15", ["MON", "WED"], "14:00", "15:15")

    def test_meridiem_inheritance_is_careful(self):
        self.assertEqual(times_of("Tue 11-12:20 pm"), ("11:00", "12:20"))           # 11 am, not 11 pm
        self.assertEqual(times_of("Tue 3:30-4:50 pm"), ("15:30", "16:50"))
        self.assertIsNone(times_of("Tue 9-10:15"))                                  # no am/pm anywhere: never guessed
        self.assertIsNone(times_of("Tue 3:30-3:00 pm"))                             # end before start
        self.assertIsNone(times_of("Tue 13:00-9:00 pm"))
        self.assertIsNone(times_of("Tue 8:99-9:30 am"))

    def test_non_meeting_lines_are_ignored(self):
        for line in ("Office Hours: T/TH 10:30 AM – 12:30 PM", "Final Exam –Tuesday, Dec 8 at 2 – 4:30 p.m.",
                     "Quiz on Thursday 3:30-4:00 pm", "TA hours: Monday 1-2 pm", "Midterm Tuesday 3:30–4:50 p.m.",
                     "Assignment due Friday 11:59 pm"):
            self.assertIsNone(one(line), line)

    def test_sentences_and_lone_letters_are_not_days(self):
        parsed = one("We will meet Tuesday and Thursday 3-4:15 pm")
        self.assertIsNone(parsed["days"])                                           # 'will' is not a filler word
        self.assertIsNone(one("T 5pm-6pm")["days"])                                  # one ambiguous letter
        self.assertIsNone(one("Students should attend regularly")) 

    def test_course_code_is_not_a_room(self):
        self.assertIsNone(one("CSE 3380 Tue 3:30-4:50 pm")["location"])
        self.assertEqual(one("CSE 3380 Tue 3:30-4:50 pm in NH 110")["location"], "NH 110")

    def test_online_meetings_are_labeled_online(self):
        self.assertEqual(one("Tue & Thu 9am to 10am (on Microsoft Teams)")["location"], "Online")


SYLLABUS_TWO_SECTIONS = """Time and Place of Class Meetings

CSE 3380-003

Fr 9:00AM - 11:50AM\t\tNH 109

Teaching Assistant

CSE 3380-004

Time and Place of Class Meetings

Th 5:30PM - 8:20PM\t\tNH 110

Teaching Assistant
"""


class ExtractionTests(unittest.TestCase):
    def test_only_the_students_section_is_suggested(self):
        mine = ss.extract_suggestions(SYLLABUS_TWO_SECTIONS, 1, course_code="2268-CSE-3380-003")["suggestions"]
        self.assertEqual([(s["days"], s["start_time"], s["location"]) for s in mine], [(["FRI"], "09:00", "NH 109")])
        other = ss.extract_suggestions(SYLLABUS_TWO_SECTIONS, 1, course_code="2268-CSE-3380-004")["suggestions"]
        self.assertEqual([(s["days"], s["location"]) for s in other], [(["THU"], "NH 110")])
        unknown = ss.extract_suggestions(SYLLABUS_TWO_SECTIONS, 1, course_code=None)["suggestions"]
        self.assertEqual(len(unknown), 2)                                            # no section known: show both, student decides

    def test_office_hours_never_become_class_times(self):
        text = ("Office Hours: Tuesday and Thursday, 8:45am – 9:15 am in UTA's Microsoft Teams. Email me to set up additional "
                "meeting times as needed.\n\nTime and Place of Class Meetings\n\nTuesday and Thursday, 9:30 am – 10:50 am.\n\n"
                "Office Hours:\n\nTuesdays 8am")
        found = ss.extract_suggestions(text, 5, course_code="2268-CSE-3311-001")["suggestions"]
        self.assertEqual([(s["days"], s["start_time"], s["end_time"]) for s in found], [(["TUE", "THU"], "09:30", "10:50")])

    def test_a_labeled_line_right_after_office_hours_is_still_read(self):
        text = "Office Hours: [Mondays 11am to 2pm], or by appointment RM 549 ERB\nClass Meetings: Tuesdays and Thursdays.\n"
        result = ss.extract_suggestions(text, 5, course_code="2268-CSE-3320-003")
        self.assertEqual(result["suggestions"], [])
        self.assertEqual([h["days"] for h in result["hints"]], [["TUE", "THU"]])    # days only: a hint, never a made-up time

    def test_location_line_is_attached_to_a_time_found_elsewhere(self):
        text = ("Meeting Time: 3:30–4:50 p.m.\nLocation: NH 229\nInstruction Mode: In-person\n\nCourse Description\n\n"
                "Students should review the posted materials during the course's regularly scheduled meeting time:\n\n"
                "Tuesday and Thursday, 3:30–4:50 p.m.\n")
        found = ss.extract_suggestions(text, 2, course_code="2268-CSE-3302-002")["suggestions"]
        self.assertEqual([(s["days"], s["start_time"], s["end_time"], s["location"]) for s in found],
                         [(["TUE", "THU"], "15:30", "16:50", "NH 229")])
        self.assertEqual(found[0]["confidence"], "high")
        self.assertIn("Tuesday and Thursday", found[0]["source_text"])               # the quoted evidence

    def test_days_and_time_on_separate_lines_combine_with_lower_confidence(self):
        text = "Class Meetings: Mondays and Wednesdays\nMeeting Time: 2:00-3:15 pm\n"
        found = ss.extract_suggestions(text, 3)["suggestions"]
        self.assertEqual([(s["days"], s["start_time"], s["confidence"]) for s in found], [(["MON", "WED"], "14:00", "medium")])
        self.assertIn("/", found[0]["source_text"])

    def test_duplicates_collapse_and_text_without_a_schedule_finds_nothing(self):
        text = "Class Meetings: Tue Thu 9:00-10:15 am\n\nClass Meetings: Tue Thu 9:00-10:15 am\n"
        self.assertEqual(len(ss.extract_suggestions(text, 1)["suggestions"]), 1)
        none = ss.extract_suggestions("Grading policy. Attendance matters. Read chapter 3 before class.", 1)
        self.assertEqual(none, {"suggestions": [], "hints": []})

    def test_fingerprint_is_stable_and_depends_on_the_meeting_and_course(self):
        a = ss.fingerprint(1, ["TUE"], "09:00", "10:00")
        self.assertEqual(a, ss.fingerprint(1, ["TUE"], "09:00", "10:00"))
        self.assertNotEqual(a, ss.fingerprint(2, ["TUE"], "09:00", "10:00"))
        self.assertRegex(a, r"^[0-9a-f]{16}$")


class ValidationTests(unittest.TestCase):
    def good(self, **over):
        return {"days": ["THU", "TUE"], "start_time": "09:30", "end_time": "10:50", **over}

    def test_valid_meeting_is_normalized(self):
        fields = ss.validate_meeting(self.good(location="  NH 229  ", kind="lab"))
        self.assertEqual((fields["days"], fields["location"], fields["kind"]), (["TUE", "THU"], "NH 229", "lab"))
        self.assertEqual(ss.validate_meeting(self.good(location="  "))["location"], None)

    def test_invalid_meetings_are_rejected_with_safe_messages(self):
        for bad in (self.good(days=[]), self.good(days=["FUNDAY"]), self.good(days="TUE"), self.good(start_time="9:30"),
                    self.good(start_time="25:00"), self.good(end_time="09:30"), self.good(end_time="08:00"),
                    self.good(kind="party"), self.good(location="x" * 121), self.good(location=5)):
            with self.assertRaises(ss.ScheduleError, msg=str(bad)):
                ss.validate_meeting(bad)


def make_store(directory, namespace="schedule-student"):
    store = IngestionStore(namespace, Path(directory) / "test.sqlite3")
    store.save_course({"course_id": 42, "synced_at": "now", "course": {"metadata": {
        "name": "SOFTWARE TESTING", "course_code": "2268-CSE-4321-002", "time_zone": "America/Chicago"}}})
    store.save_sync_state({"courses": [{"course_id": 42}], "status": "completed"})
    store.save_document(42, {"course_id": 42, "document_key": "syllabus_file:abc", "canvas_id": 1, "title": "Syllabus.pdf",
                             "source_type": "syllabus_file", "status": "ok", "version": "1", "pages": [],
                             "text": "Time and Place of Class Meetings\n\nTue & Thu, 11am to 12.20pm, WH 311\n\nTime Zone\n\n"
                                     "This course operates on Central Time."}, "now")
    ensure_course_chunks(42, store)
    return store


class ScheduleApiTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.store = make_store(self.directory.name)
        patch.object(main, "default_store", return_value=self.store).start()
        self.addCleanup(patch.stopall)

    def call(self, method, path, body=None):
        return asyncio.run(request(method, path, body))

    def course(self):
        return self.call("GET", "/api/schedule")[1]["courses"][0]

    def test_get_suggests_from_the_syllabus_without_saving_anything(self):
        course = self.course()
        self.assertEqual((course["course_id"], course["time_zone"], course["name"]), (42, "America/Chicago", "SOFTWARE TESTING"))
        self.assertEqual([(s["days"], s["start_time"], s["end_time"], s["location"]) for s in course["suggestions"]],
                         [(["TUE", "THU"], "11:00", "12:20", "WH 311")])
        self.assertEqual(course["meetings"], [])
        self.assertEqual(self.store.list_class_meetings(), [])                      # a suggestion is never trusted data

    def test_confirming_a_suggestion_creates_a_meeting_and_hides_the_suggestion(self):
        suggestion = self.course()["suggestions"][0]
        code, data = self.call("POST", "/api/schedule/meetings", {
            "course_id": 42, "days": suggestion["days"], "start_time": suggestion["start_time"], "end_time": suggestion["end_time"],
            "location": suggestion["location"], "source": "syllabus", "source_text": suggestion["source_text"],
            "fingerprint": suggestion["fingerprint"]})
        self.assertEqual(code, 201)
        self.assertEqual((data["status"], data["source"], data["fingerprint"]), ("confirmed", "syllabus", suggestion["fingerprint"]))
        course = self.course()
        self.assertEqual(len(course["meetings"]), 1)
        self.assertEqual(course["suggestions"], [])

    def test_editing_a_confirmed_meeting_does_not_bring_the_suggestion_back(self):
        suggestion = self.course()["suggestions"][0]
        created = self.call("POST", "/api/schedule/meetings", {"course_id": 42, **{k: suggestion[k] for k in ("days", "start_time", "end_time")},
                                                              "source": "syllabus", "fingerprint": suggestion["fingerprint"]})[1]
        code, edited = self.call("PUT", f"/api/schedule/meetings/{created['meeting_id']}", {"start_time": "11:15", "location": "NH 110"})
        self.assertEqual((code, edited["start_time"], edited["location"], edited["end_time"]), (200, "11:15", "NH 110", "12:20"))
        self.assertEqual(self.course()["suggestions"], [])

    def test_manual_entry_create_edit_delete(self):
        code, made = self.call("POST", "/api/schedule/meetings", {"course_id": 42, "days": ["WED"], "start_time": "17:30",
                                                                  "end_time": "19:00", "kind": "lab", "location": " ERB 100 "})
        self.assertEqual((code, made["source"], made["kind"], made["location"]), (201, "manual", "lab", "ERB 100"))
        self.assertEqual(len(self.course()["meetings"]), 1)
        self.assertEqual(self.call("PUT", f"/api/schedule/meetings/{made['meeting_id']}", {"days": ["MON", "WED"]})[1]["days"], ["MON", "WED"])
        self.assertEqual(self.call("DELETE", f"/api/schedule/meetings/{made['meeting_id']}"), (200, {"ok": True}))
        self.assertEqual(self.course()["meetings"], [])
        self.assertEqual(self.call("DELETE", f"/api/schedule/meetings/{made['meeting_id']}")[0], 404)

    def test_dismissing_hides_a_suggestion_and_restoring_brings_it_back(self):
        suggestion = self.course()["suggestions"][0]
        self.assertEqual(self.call("POST", "/api/schedule/suggestions/dismiss",
                                   {"course_id": 42, "fingerprint": suggestion["fingerprint"]})[0], 200)
        course = self.course()
        self.assertEqual((course["suggestions"], course["dismissed_count"]), ([], 1))
        self.assertEqual(self.store.list_class_meetings("confirmed"), [])           # dismissed never counts as a meeting
        self.assertEqual(self.call("DELETE", "/api/schedule/courses/42/dismissed")[0], 200)
        self.assertEqual(len(self.course()["suggestions"]), 1)

    def test_invalid_requests_are_refused_and_nothing_is_saved(self):
        good = {"course_id": 42, "days": ["TUE"], "start_time": "09:00", "end_time": "10:00"}
        cases = [({**good, "end_time": "08:00"}, 422), ({**good, "days": []}, 422), ({**good, "start_time": "9am"}, 422),
                 ({**good, "kind": "party"}, 422), ({**good, "extra": 1}, 422), ({**good, "course_id": "42"}, 422),
                 ({**good, "fingerprint": "not-a-fingerprint"}, 422), ({**good, "source": "ai"}, 422),
                 ({**good, "course_id": 999}, 404)]
        for body, expected in cases:
            self.assertEqual(self.call("POST", "/api/schedule/meetings", body)[0], expected, body)
        self.assertEqual(self.call("PUT", "/api/schedule/meetings/not-an-id", {"days": ["TUE"]})[0], 422)
        self.assertEqual(self.call("PUT", "/api/schedule/meetings/" + "a" * 32, {"days": ["TUE"]})[0], 404)
        self.assertEqual(self.call("POST", "/api/schedule/suggestions/dismiss", {"course_id": 999, "fingerprint": "a" * 16})[0], 404)
        self.assertEqual(self.call("DELETE", "/api/schedule/courses/999/dismissed")[0], 404)
        self.assertEqual(self.store.list_class_meetings(), [])

    def test_meetings_are_isolated_between_accounts(self):
        self.call("POST", "/api/schedule/meetings", {"course_id": 42, "days": ["TUE"], "start_time": "09:00", "end_time": "10:00"})
        other = make_store(self.directory.name, namespace="someone-else")
        self.assertEqual(other.list_class_meetings(), [])
        self.assertEqual(len(self.store.list_class_meetings()), 1)

    def test_meetings_survive_a_reopened_database(self):
        made = self.call("POST", "/api/schedule/meetings", {"course_id": 42, "days": ["FRI"], "start_time": "09:00", "end_time": "11:50"})[1]
        reopened = IngestionStore("schedule-student", self.store.path)
        self.assertEqual([m["meeting_id"] for m in reopened.list_class_meetings("confirmed")], [made["meeting_id"]])


if __name__ == "__main__":
    unittest.main()


import backend.tests.ai_consent_granted  # noqa: E402,F401  (AI tests use fake providers)
