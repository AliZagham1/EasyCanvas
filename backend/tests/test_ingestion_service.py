import unittest
from contextlib import ExitStack
from unittest.mock import Mock, patch

import requests

from backend.app import canvas_service, ingestion_service


class IngestionTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.fetchers = {}
        fixtures = {
            "get_course_details": {"id": 42, "course_code": "TEST", "syllabus_body": "<p>Welcome</p>"},
            "get_assignments": [{"id": 10, "name": "Outside modules", "updated_at": "2026-10-01"}],
            "get_modules": [{"id": 7, "name": "Week One"}],
            "get_module_items": [{"id": 20, "title": "Linked work", "type": "Assignment", "content_id": 10}],
            "get_pages": [{"page_id": 30, "title": "Welcome", "url": "welcome", "html_url": "https://canvas.example/welcome"}],
            "get_files": [{"id": 40, "display_name": "Lecture.pdf", "modified_at": "2026-10-02"}],
            "get_quizzes": [{"id": 50, "title": "Quiz"}],
            "get_announcements": [{"id": 60, "title": "News", "posted_at": "2026-10-03"}],
            "get_discussions": [{"id": 70, "title": "Discussion"}],
        }
        for name, value in fixtures.items():
            self.fetchers[name] = self.stack.enter_context(
                patch.object(canvas_service, name, return_value=value)
            )

    def test_normalizes_all_sources_without_duplicate_requests(self):
        result = ingestion_service.sync_course(42)
        for name, fetch in self.fetchers.items():
            fetch.assert_called_once_with(*((42, 7) if name == "get_module_items" else (42,)))
        self.assertEqual(result["syllabus"]["body"], "<p>Welcome</p>")
        self.assertEqual(result["assignments"][0]["updated_at"], "2026-10-01")
        self.assertIsNone(result["assignments"][0]["module_id"])
        self.assertEqual(result["pages"][0]["canvas_id"], 30)
        self.assertEqual(result["pages"][0]["url"], "https://canvas.example/welcome")
        self.assertEqual(result["files"][0]["metadata"]["modified_at"], "2026-10-02")
        self.assertEqual(result["announcements"][0]["metadata"]["posted_at"], "2026-10-03")
        self.assertEqual(result["module_items"][0]["module_id"], 7)
        self.assertEqual(result["module_items"][0]["module_name"], "Week One")
        self.assertEqual(result["module_items"][0]["content_id"], 10)
        self.assertEqual(result["module_items"][0]["item_type"], "Assignment")
        for source in ("assignments", "modules", "module_items", "pages", "files", "quizzes", "announcements", "discussions"):
            self.assertEqual(result[source][0]["course_id"], 42)
            self.assertEqual(result["source_status"][source]["status"], "ok")
        self.assertIn("Files: 1", ingestion_service.format_sync_summary(result))

    def test_sources_are_discovered_without_modules(self):
        self.fetchers["get_modules"].return_value = []
        result = ingestion_service.sync_course(42)
        self.fetchers["get_module_items"].assert_not_called()
        self.assertEqual(len(result["assignments"]), 1)
        self.assertEqual(len(result["pages"]), 1)
        self.assertEqual(result["module_items"], [])

    def test_unavailable_sources_are_distinct_from_empty_sources(self):
        response = Mock(status_code=403)
        self.fetchers["get_files"].side_effect = requests.HTTPError(response=response)
        self.fetchers["get_pages"].return_value = []
        self.fetchers["get_quizzes"].side_effect = requests.ConnectionError()
        result = ingestion_service.sync_course(42)
        self.assertEqual(result["source_status"]["files"]["status"], "unavailable")
        self.assertEqual(result["source_status"]["pages"]["status"], "ok")
        self.assertEqual(result["source_status"]["quizzes"]["status"], "error")
        summary = ingestion_service.format_sync_summary(result)
        self.assertIn("Files: unavailable", summary)
        self.assertIn("Files: unavailable (HTTP 403)", summary)
        self.assertIn("Pages: 0", summary)
        self.assertIn("Quizzes: error", summary)

    def test_module_failure_marks_item_discovery_partial(self):
        self.fetchers["get_module_items"].side_effect = requests.HTTPError(response=Mock(status_code=404))
        result = ingestion_service.sync_course(42)
        self.assertEqual(result["source_status"]["module_items"]["status"], "partial")
        self.assertIn("Module items: 0 (partial)", ingestion_service.format_sync_summary(result))

    def test_missing_syllabus_does_not_trigger_another_request(self):
        self.fetchers["get_course_details"].return_value = {"id": 42}
        result = ingestion_service.sync_course(42)
        self.assertIsNone(result["syllabus"])
        self.assertEqual(result["source_status"]["syllabus"]["status"], "unavailable")
        self.fetchers["get_course_details"].assert_called_once_with(42)

    def test_referenced_page_is_recovered_when_page_index_is_unavailable(self):
        self.fetchers["get_pages"].side_effect = requests.HTTPError(response=Mock(status_code=403))
        self.fetchers["get_module_items"].return_value = [
            {"id": 1, "type": "Page", "page_url": "welcome"},
            {"id": 2, "type": "Page", "page_url": "welcome"},
        ]
        with patch.object(canvas_service, "CANVAS_URL", "https://canvas.example"), \
             patch.object(canvas_service, "get_page", return_value={"page_id": 30, "url": "welcome", "body": '<a href="/files/9/download">Read</a>'}) as get_page:
            result = ingestion_service.sync_course(42, include_page_bodies=True)
        get_page.assert_called_once_with(42, "welcome")
        self.assertEqual(result["source_status"]["pages"]["status"], "unavailable")
        self.assertIn(9, [item["file_id"] for item in result["file_inventory"]])

    def test_page_body_from_listing_does_not_need_another_page_request(self):
        self.fetchers["get_pages"].return_value = [{"page_id": 30, "url": "welcome", "body": "<p>Welcome</p>"}]
        with patch.object(canvas_service, "get_page") as get_page:
            result = ingestion_service.sync_course(42, include_page_bodies=True)
        get_page.assert_not_called()
        self.fetchers["get_pages"].assert_called_once_with(42, include_body=True)
        self.assertEqual(result["source_status"]["page_bodies"]["status"], "ok")


class CanvasSourceTests(unittest.TestCase):
    def test_all_list_endpoints_follow_pagination(self):
        for name, path, extra in (
            ("get_courses", "/courses", {}),
            ("get_assignments", "/courses/42/assignments", {"include[]": "submission"}),
            ("get_pages", "/courses/42/pages", {}),
            ("get_files", "/courses/42/files", {}),
            ("get_quizzes", "/courses/42/quizzes", {}),
            ("get_announcements", "/courses/42/discussion_topics", {"only_announcements": "true"}),
            ("get_discussions", "/courses/42/discussion_topics", {}),
        ):
            with self.subTest(endpoint=name):
                first = Mock(links={"next": {"url": "https://canvas.example/next?page=2"}})
                first.json.return_value = [{"id": 1}]
                second = Mock(links={})
                second.json.return_value = [{"id": 2}]
                with patch.object(canvas_service, "CANVAS_URL", "https://canvas.example"), \
                     patch.object(canvas_service.requests, "get", side_effect=[first, second]) as get:
                    records = getattr(canvas_service, name)(*(() if name == "get_courses" else (42,)))
                self.assertEqual(records, [{"id": 1}, {"id": 2}])
                self.assertEqual(get.call_args_list[0].args, (f"https://canvas.example/api/v1{path}",))
                self.assertEqual(get.call_args_list[0].kwargs["params"], {"per_page": 100, **extra})
                self.assertEqual(get.call_args_list[1].args, ("https://canvas.example/next?page=2",))
                self.assertIsNone(get.call_args_list[1].kwargs["params"])
                first.raise_for_status.assert_called_once()
                second.raise_for_status.assert_called_once()

    def test_course_details_includes_syllabus(self):
        response = Mock()
        response.json.return_value = {"id": 42, "syllabus_body": "Hello"}
        with patch.object(canvas_service, "CANVAS_URL", "https://canvas.example"), \
             patch.object(canvas_service.requests, "get", return_value=response) as get:
            self.assertEqual(canvas_service.get_course_details(42), {"id": 42, "syllabus_body": "Hello"})
        self.assertEqual(get.call_args.args, ("https://canvas.example/api/v1/courses/42",))
        self.assertEqual(get.call_args.kwargs["params"], {"include[]": "syllabus_body"})
        response.raise_for_status.assert_called_once()


if __name__ == "__main__":
    unittest.main()
