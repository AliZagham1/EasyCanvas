import unittest
from unittest.mock import Mock, call, patch

import requests

from backend.app import canvas_service


def make_response(data, next_url=None):
    response = Mock()
    response.json.return_value = data
    response.links = {"next": {"url": next_url}} if next_url else {}
    return response


class CourseContentTests(unittest.TestCase):
    def test_discovery_follows_pagination(self):
        for function, arguments, path in (
            (canvas_service.get_modules, (42,), "/courses/42/modules"),
            (canvas_service.get_module_items, (42, 7), "/courses/42/modules/7/items"),
        ):
            with self.subTest(function=function.__name__):
                url = f"https://canvas.example/api/v1{path}"
                next_url = f"{url}?page=2&per_page=100"
                first = make_response([{"id": 1}], next_url)
                second = make_response([{"id": 2}])
                with patch.object(canvas_service, "CANVAS_URL", "https://canvas.example"), \
                     patch.object(canvas_service, "headers", {"Authorization": "Bearer test"}), \
                     patch.object(canvas_service.requests, "get", side_effect=[first, second]) as get:
                    self.assertEqual(function(*arguments), [{"id": 1}, {"id": 2}])
                    self.assertEqual(get.call_args_list, [
                        call(url, headers={"Authorization": "Bearer test"}, params={"per_page": 100}, timeout=canvas_service.REQUEST_TIMEOUT),
                        call(next_url, headers={"Authorization": "Bearer test"}, params=None, timeout=canvas_service.REQUEST_TIMEOUT),
                    ])
                first.raise_for_status.assert_called_once()
                second.raise_for_status.assert_called_once()

    def test_clean_structure_preserves_modules_and_optional_fields(self):
        modules = [{"id": 7, "name": "Slides"}, {"id": 8, "name": "Empty"}]
        items = [
            {"id": 9, "title": "Lecture.pdf", "type": "File", "content_id": 10,
             "url": "https://canvas.example/api/v1/files/10", "position": 1},
            {"id": 11, "title": "Week One", "type": "SubHeader"},
        ]
        with patch.object(canvas_service, "get_modules", return_value=modules) as get_modules, \
             patch.object(canvas_service, "get_module_items", side_effect=[items, []]) as get_items:
            result = canvas_service.get_course_content(42)
        get_modules.assert_called_once_with(42)
        self.assertEqual(get_items.call_args_list, [call(42, 7), call(42, 8)])
        self.assertEqual(result, [
            {"module_id": 7, "module_name": "Slides", "items": [
                {"id": 9, "title": "Lecture.pdf", "type": "File", "content_id": 10,
                 "url": "https://canvas.example/api/v1/files/10"},
                {"id": 11, "title": "Week One", "type": "SubHeader", "content_id": None, "url": None},
            ]},
            {"module_id": 8, "module_name": "Empty", "items": []},
        ])

    def test_course_without_modules(self):
        with patch.object(canvas_service, "get_modules", return_value=[]), \
             patch.object(canvas_service, "get_module_items") as get_items:
            self.assertEqual(canvas_service.get_course_content(42), [])
            get_items.assert_not_called()

    def test_http_errors_are_not_returned_as_content(self):
        for function, arguments in (
            (canvas_service.get_modules, (42,)),
            (canvas_service.get_module_items, (42, 7)),
        ):
            with self.subTest(function=function.__name__):
                response = make_response({"errors": ["Unauthorized"]})
                response.raise_for_status.side_effect = requests.HTTPError("Unauthorized")
                with patch.object(canvas_service.requests, "get", return_value=response):
                    with self.assertRaises(requests.HTTPError):
                        function(*arguments)
                response.json.assert_not_called()


if __name__ == "__main__":
    unittest.main()
