"""Course pages: saved announcements, modules, counts and links, assembled without Canvas or AI."""
import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.app import canvas_service, course_overview_service as cos, main, schedule_service
from backend.app.ingestion_store import IngestionStore
from backend.tests.test_retention_api import request

CANVAS = "https://canvas.example.edu"


def announcement(title, posted, message="<p>Hello</p>", url="https://canvas.example.edu/courses/42/discussion_topics/1"):
    return {"title": title, "url": url, "created_at": "2026-01-01T00:00:00Z", "metadata": {"posted_at": posted, "message": message}}


def snapshot(course_id=42, name="SOFTWARE TESTING", code="2268-CSE-4321-002"):
    return {"course_id": course_id, "synced_at": "2026-11-17T20:00:00Z",
            "course": {"metadata": {"name": name, "course_code": code, "time_zone": "America/Chicago"}},
            "assignments": [{}, {}, {}], "pages": [{}], "quizzes": [], "files": [{}, {}], "file_inventory": [{}, {}, {}, {}],
            "announcements": [announcement("Old news", "2026-09-01T10:00:00Z"), announcement("Latest", "2026-11-10T10:00:00Z", "<script>alert(1)</script><b>Bold</b> &amp; more"),
                              announcement("Middle", "2026-10-05T10:00:00Z")],
            # Real saved module records carry their id in canvas_id; module_id is empty. Items point at that id.
            "modules": [{"canvas_id": 2, "module_id": None, "title": "Week 2", "metadata": {"id": 2, "position": 2}},
                        {"canvas_id": 1, "module_id": None, "title": "Week 1", "metadata": {"id": 1, "position": 1}},
                        {"canvas_id": 3, "module_id": None, "title": "Hidden", "metadata": {"id": 3, "position": 3, "published": False}}],
            "module_items": [
                {"module_id": 1, "title": "Slides B", "item_type": "File", "url": f"{CANVAS}/courses/42/modules/items/12", "metadata": {"type": "File", "position": 2}},
                {"module_id": 1, "title": "Slides A", "item_type": "File", "url": f"{CANVAS}/courses/42/modules/items/11", "metadata": {"type": "File", "position": 1}},
                {"module_id": 1, "title": "Section header", "item_type": "SubHeader", "metadata": {"type": "SubHeader", "position": 3}},
                {"module_id": 1, "title": "Draft page", "item_type": "Page", "metadata": {"type": "Page", "position": 4, "published": False}},
                {"module_id": 2, "title": "Reading", "item_type": "Page", "url": None, "metadata": {"type": "Page", "position": 1, "html_url": f"{CANVAS}/courses/42/modules/items/21"}}]}


def make_store(directory, namespace="overview-student"):
    store = IngestionStore(namespace, Path(directory) / "test.sqlite3")
    store.save_course(snapshot())
    store.save_course(snapshot(43, "DATABASES", "2268-CSE-3330-001"))
    store.save_sync_state({"courses": [{"course_id": 42}, {"course_id": 43}], "status": "completed"})
    return store


class PlainTextTests(unittest.TestCase):
    def test_html_becomes_safe_plain_text(self):
        self.assertEqual(cos.plain_text("<p>Hi <b>there</b></p><script>alert('x')</script><style>p{}</style>  &amp; <a href='x'>link</a>"), "Hi there & link")
        self.assertNotIn("<", cos.plain_text("<img src=x onerror=alert(1)>text"))
        self.assertEqual(cos.plain_text("a\n\n  b\t c"), "a b c")

    def test_long_text_is_shortened_and_non_text_is_empty(self):
        text = cos.plain_text("word " * 200)
        self.assertLessEqual(len(text), cos.MAX_EXCERPT)
        self.assertTrue(text.endswith("…"))
        for value in (None, 5, {"a": 1}, []):
            self.assertEqual(cos.plain_text(value), "")


class CatalogAndDetailsTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.store = make_store(self.directory.name)
        patch.object(canvas_service, "CANVAS_URL", CANVAS + "/").start()
        self.addCleanup(patch.stopall)

    def test_catalog_summarizes_each_saved_course(self):
        courses = {c["course_id"]: c for c in cos.build_catalog(self.store)["courses"]}
        self.assertEqual(set(courses), {42, 43})
        first = courses[42]
        self.assertEqual((first["course_code"], first["name"], first["time_zone"]), ("2268-CSE-4321-002", "SOFTWARE TESTING", "America/Chicago"))
        self.assertEqual(first["canvas_url"], f"{CANVAS}/courses/42")                # trailing slash on the base is handled
        self.assertEqual(first["counts"], {"assignments": 3, "modules": 3, "files": 4, "pages": 1, "quizzes": 0, "announcements": 3})
        self.assertEqual(first["latest_announcement"]["title"], "Latest")
        self.assertEqual(first["synced_at"], "2026-11-17T20:00:00Z")

    def test_catalog_includes_only_confirmed_meetings(self):
        schedule_service.create_meeting(self.store, 42, {"days": ["TUE"], "start_time": "09:00", "end_time": "10:00"})
        schedule_service.dismiss_suggestion(self.store, 42, "a" * 16)
        schedule_service.create_meeting(self.store, 43, {"days": ["FRI"], "start_time": "11:00", "end_time": "12:00"})
        courses = {c["course_id"]: c for c in cos.build_catalog(self.store)["courses"]}
        self.assertEqual([m["days"] for m in courses[42]["meetings"]], [["TUE"]])
        self.assertEqual([m["days"] for m in courses[43]["meetings"]], [["FRI"]])

    def test_study_blocks_are_not_listed_as_class_times_on_course_pages(self):
        schedule_service.create_meeting(self.store, 42, {"days": ["TUE"], "start_time": "09:00", "end_time": "10:00"})
        schedule_service.create_meeting(self.store, 42, {"days": ["WED"], "start_time": "14:00", "end_time": "16:00", "kind": "study"})
        course = {c["course_id"]: c for c in cos.build_catalog(self.store)["courses"]}[42]
        self.assertEqual([m["days"] for m in course["meetings"]], [["TUE"]])
        self.assertEqual([m["days"] for m in cos.build_details(self.store, 42)["meetings"]], [["TUE"]])

    def test_a_course_without_content_still_appears_with_zero_counts(self):
        store = IngestionStore("empty-student", Path(self.directory.name) / "empty.sqlite3")
        store.save_course({"course_id": 7, "synced_at": "now", "course": {"metadata": {"name": "X", "course_code": "2268-CSE-1000-001"}}})
        store.save_sync_state({"courses": [{"course_id": 7}], "status": "completed"})
        course = cos.build_catalog(store)["courses"][0]
        self.assertEqual(set(course["counts"].values()), {0})
        self.assertIsNone(course["latest_announcement"])

    def test_details_announcements_are_newest_first_plain_and_linked(self):
        details = cos.build_details(self.store, 42)
        self.assertEqual([a["title"] for a in details["announcements"]], ["Latest", "Middle", "Old news"])
        self.assertEqual(details["announcements"][0]["excerpt"], "Bold & more")      # script removed, entity decoded, no tags
        self.assertTrue(all("<" not in a["excerpt"] for a in details["announcements"]))
        self.assertEqual(details["announcements"][0]["url"], "https://canvas.example.edu/courses/42/discussion_topics/1")

    def test_details_modules_are_ordered_and_leave_out_headers_and_unpublished_items(self):
        modules = cos.build_details(self.store, 42)["modules"]
        self.assertEqual([m["name"] for m in modules], ["Week 1", "Week 2"])         # Hidden module dropped, Canvas order kept
        self.assertEqual([i["title"] for i in modules[0]["items"]], ["Slides A", "Slides B"])
        self.assertEqual(modules[1]["items"][0]["url"], f"{CANVAS}/courses/42/modules/items/21")   # falls back to the item's html_url
        self.assertTrue(all(set(i) == {"title", "type", "url"} for m in modules for i in m["items"]))

    def test_every_module_has_a_unique_id_and_its_own_items(self):
        """Regression: module records have module_id=None, which gave every module a null id and no items."""
        modules = cos.build_details(self.store, 42)["modules"]
        ids = [m["module_id"] for m in modules]
        self.assertTrue(all(i is not None for i in ids))
        self.assertEqual(len(set(ids)), len(ids))
        self.assertEqual(ids, [1, 2])
        self.assertEqual([len(m["items"]) for m in modules], [2, 1])

    def test_a_module_without_any_id_still_gets_a_unique_one(self):
        snap = snapshot(44, "NO IDS", "2268-CSE-1111-001")
        snap["modules"] = [{"title": "A", "metadata": {}}, {"title": "B", "metadata": {}}]
        snap["module_items"] = []
        self.store.save_course(snap)
        self.store.save_sync_state({"courses": [{"course_id": 42}, {"course_id": 43}, {"course_id": 44}], "status": "completed"})
        ids = [m["module_id"] for m in cos.build_details(self.store, 44)["modules"]]
        self.assertEqual(len(set(ids)), 2)

    def test_details_links_and_unknown_course(self):
        details = cos.build_details(self.store, 42)
        self.assertEqual((details["canvas_url"], details["syllabus_url"]), (f"{CANVAS}/courses/42", f"{CANVAS}/courses/42/assignments/syllabus"))
        with self.assertRaises(LookupError):
            cos.build_details(self.store, 999)

    def test_links_are_absent_when_canvas_url_is_not_configured(self):
        with patch.object(canvas_service, "CANVAS_URL", None):
            details = cos.build_details(self.store, 42)
        self.assertEqual((details["canvas_url"], details["syllabus_url"]), (None, None))


class OverviewApiTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.store = make_store(self.directory.name)
        patch.object(main, "default_store", return_value=self.store).start()
        patch.object(canvas_service, "CANVAS_URL", CANVAS).start()
        self.addCleanup(patch.stopall)

    def call(self, path):
        return asyncio.run(request("GET", path))

    def test_catalog_and_details_endpoints(self):
        code, catalog = self.call("/api/course-catalog")
        self.assertEqual((code, sorted(c["course_id"] for c in catalog["courses"])), (200, [42, 43]))
        code, details = self.call("/api/courses/42/details")
        self.assertEqual((code, details["course_id"], len(details["modules"])), (200, 42, 2))

    def test_unknown_and_invalid_course_ids_are_refused(self):
        self.assertEqual(self.call("/api/courses/999/details")[0], 404)
        self.assertEqual(self.call("/api/courses/0/details")[0], 422)
        self.assertEqual(self.call("/api/courses/abc/details")[0], 422)

    def test_data_is_isolated_between_accounts(self):
        other = IngestionStore("someone-else", Path(self.directory.name) / "test.sqlite3")
        self.assertEqual(cos.build_catalog(other), {"courses": []})
        with patch.object(main, "default_store", return_value=other):
            self.assertEqual(self.call("/api/courses/42/details")[0], 404)


if __name__ == "__main__":
    unittest.main()


import backend.tests.ai_consent_granted  # noqa: E402,F401  (AI tests use fake providers)
