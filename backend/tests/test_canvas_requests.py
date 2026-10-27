import unittest
from unittest.mock import Mock, patch

import requests

from backend.app import canvas_service


class CanvasRequestTests(unittest.TestCase):
    def test_retries_rate_limit_then_succeeds(self):
        limited = Mock(status_code=429, headers={"Retry-After": "2"})
        success = Mock(status_code=200)
        with patch.object(canvas_service.requests, "get", side_effect=[limited, success]) as get, \
             patch.object(canvas_service.time, "sleep") as sleep:
            self.assertIs(canvas_service._request_get("https://canvas.example/api"), success)
        self.assertEqual(get.call_count, 2)
        self.assertEqual(get.call_args.kwargs["timeout"], 60)
        sleep.assert_called_once_with(2)
        limited.close.assert_called_once()

    def test_retry_count_is_bounded(self):
        with patch.object(canvas_service.requests, "get", side_effect=requests.Timeout()) as get, \
             patch.object(canvas_service.time, "sleep"):
            with self.assertRaises(requests.Timeout):
                canvas_service._request_get("https://canvas.example/api")
        self.assertEqual(get.call_count, 3)

    def test_permission_failure_is_not_retried(self):
        denied = Mock(status_code=403)
        denied.raise_for_status.side_effect = requests.HTTPError(response=denied)
        with patch.object(canvas_service.requests, "get", return_value=denied) as get:
            with self.assertRaises(requests.HTTPError):
                canvas_service._request_get("https://canvas.example/api")
        get.assert_called_once()

    def test_page_bodies_requested_in_one_paginated_listing(self):
        response = Mock(status_code=200, links={})
        response.json.return_value = [{"page_id": 1, "body": "Content"}]
        with patch.object(canvas_service, "CANVAS_URL", "https://canvas.example"), \
             patch.object(canvas_service.requests, "get", return_value=response) as get:
            self.assertEqual(canvas_service.get_pages(42, include_body=True)[0]["body"], "Content")
        self.assertEqual(get.call_args.kwargs["params"], {"per_page": 100, "include[]": "body"})

    def test_malformed_list_is_reported_as_request_failure(self):
        response = Mock(status_code=200)
        response.json.return_value = {"error": "Unexpected response"}
        with patch.object(canvas_service.requests, "get", return_value=response):
            with self.assertRaises(requests.RequestException):
                canvas_service.get_files(42)

    def test_later_page_failure_retains_successful_records(self):
        first = Mock(status_code=200, links={"next": {"url": "https://canvas.example/next"}})
        first.json.return_value = [{"id": 9}]
        with patch.object(canvas_service, "CANVAS_URL", "https://canvas.example"), \
             patch.object(canvas_service.requests, "get", side_effect=[first, requests.HTTPError()]):
            with self.assertRaises(canvas_service.CanvasPartialResponse) as raised:
                canvas_service.get_files(42)
        self.assertEqual(raised.exception.records, [{"id": 9}])

    def test_foreign_pagination_link_is_never_requested_with_token(self):
        first = Mock(status_code=200, links={"next": {"url": "https://evil.example/next"}})
        first.json.return_value = [{"id": 9}]
        with patch.object(canvas_service, "CANVAS_URL", "https://canvas.example"), \
             patch.object(canvas_service.requests, "get", return_value=first) as get:
            with self.assertRaises(canvas_service.CanvasPartialResponse):
                canvas_service.get_files(42)
        get.assert_called_once()


if __name__ == "__main__":
    unittest.main()
