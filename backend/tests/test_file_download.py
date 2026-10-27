import unittest
from unittest.mock import Mock, patch

import requests

from backend.app import canvas_service


class FileDownloadTests(unittest.TestCase):
    def test_individual_metadata_endpoint(self):
        response = Mock()
        response.json.return_value = {"id": 9, "url": "https://canvas.example/download"}
        with patch.object(canvas_service, "CANVAS_URL", "https://canvas.example"), \
             patch.object(canvas_service.requests, "get", return_value=response) as get:
            metadata = canvas_service.get_file_info(42, 9)
        self.assertEqual(metadata["id"], 9)
        get.assert_called_once_with(
            "https://canvas.example/api/v1/courses/42/files/9",
            headers=canvas_service.headers, timeout=60,
        )
        response.raise_for_status.assert_called_once()

    def test_download_authenticates_only_to_canvas_origin(self):
        for download_url, authenticated in (
            ("https://canvas.example/files/9/download", True),
            ("https://storage.example/signed-file", False),
            ("http://canvas.example/files/9/download", False),
            ("https://canvas.example:8443/files/9/download", False),
        ):
            with self.subTest(url=download_url):
                metadata = {"id": 9, "url": download_url}
                response = Mock()
                response.iter_content.return_value = [b"%PDF-test bytes"]
                with patch.object(canvas_service, "CANVAS_URL", "https://canvas.example"), \
                     patch.object(canvas_service, "get_file_info", return_value=metadata) as info, \
                     patch.object(canvas_service.requests, "get", return_value=response) as get:
                    self.assertEqual(canvas_service.download_canvas_file(42, 9), (metadata, b"%PDF-test bytes"))
                info.assert_called_once_with(42, 9)
                get.assert_called_once_with(
                    download_url, headers=canvas_service.headers if authenticated else {},
                    allow_redirects=True, stream=True, timeout=60,
                )
                response.raise_for_status.assert_called_once()

    def test_requests_strips_canvas_token_on_storage_redirect(self):
        previous = requests.Request("GET", "https://canvas.example/download").prepare()
        response = Mock(request=previous)
        redirected = requests.Request(
            "GET", "https://storage.example/signed-file",
            headers={"Authorization": "Bearer test"},
        ).prepare()
        with requests.Session() as session:
            session.trust_env = False
            session.rebuild_auth(redirected, response)
        self.assertNotIn("Authorization", redirected.headers)

    def test_missing_url_does_not_start_download(self):
        with patch.object(canvas_service, "get_file_info", return_value={"id": 9}), \
             patch.object(canvas_service.requests, "get") as get:
            with self.assertRaises(ValueError):
                canvas_service.download_canvas_file(42, 9)
            get.assert_not_called()

    def test_metadata_failure_stops_download(self):
        response = Mock()
        response.raise_for_status.side_effect = requests.HTTPError("Forbidden")
        with patch.object(canvas_service.requests, "get", return_value=response) as get:
            with self.assertRaises(requests.HTTPError):
                canvas_service.download_canvas_file(42, 9)
            self.assertEqual(get.call_count, 1)
        response.json.assert_not_called()

    def test_download_http_failure_propagates(self):
        response = Mock()
        response.raise_for_status.side_effect = requests.HTTPError("Forbidden")
        with patch.object(canvas_service, "get_file_info", return_value={"url": "https://storage.example/file"}), \
             patch.object(canvas_service.requests, "get", return_value=response):
            with self.assertRaises(requests.HTTPError):
                canvas_service.download_canvas_file(42, 9)

    def test_known_metadata_avoids_duplicate_lookup(self):
        metadata = {"id": 9, "url": "https://storage.example/signed"}
        response = Mock(status_code=200)
        response.iter_content.return_value = [b"hello"]
        with patch.object(canvas_service, "get_file_info") as info, \
             patch.object(canvas_service.requests, "get", return_value=response):
            self.assertEqual(canvas_service.download_canvas_file(42, 9, metadata=metadata), (metadata, b"hello"))
        info.assert_not_called()
        response.close.assert_called_once()

    def test_oversized_stream_stops_and_closes_response(self):
        response = Mock(status_code=200)
        response.iter_content.return_value = [b"ab", b"cd"]
        with patch.object(canvas_service.requests, "get", return_value=response):
            with self.assertRaises(ValueError):
                canvas_service.download_canvas_file(42, 9, metadata={"id": 9, "url": "https://storage.example/file"}, max_bytes=3)
        response.close.assert_called_once()

    def test_wrong_file_or_known_large_file_is_not_downloaded(self):
        with patch.object(canvas_service.requests, "get") as get:
            for metadata in ({"id": 10}, {"id": 9, "size": 20}):
                with self.assertRaises(ValueError):
                    canvas_service.download_canvas_file(42, 9, metadata=metadata, max_bytes=10)
        get.assert_not_called()


if __name__ == "__main__":
    unittest.main()
