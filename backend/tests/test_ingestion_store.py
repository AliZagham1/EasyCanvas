import tempfile
import unittest
from pathlib import Path

from backend.app.ingestion_store import IngestionStore


class IngestionStoreTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "ingestion.sqlite3"
        self.store = IngestionStore("student-a", self.path)
        self.snapshot = {"course_id": 42, "synced_at": "first", "source_status": {"files": {"status": "unavailable"}}}
        self.store.save_course(self.snapshot)
        self.document = {
            "document_key": "file:9", "canvas_id": 9, "course_id": 42,
            "source_type": "file", "status": "ok", "version": "v1",
            "text": "First page\n\nSecond page", "pages": [
                {"page_number": 1, "text": "First page", "status": "ok"},
                {"page_number": 2, "text": "Second page", "status": "ok"},
            ],
        }

    def tearDown(self):
        self.directory.cleanup()

    def test_data_survives_a_new_store_instance_and_keeps_page_numbers(self):
        self.store.save_document(42, self.document, "first")
        reopened = IngestionStore("student-a", self.path)
        self.assertEqual(reopened.load_course(42), self.snapshot)
        loaded = reopened.load_document(42, "file:9")
        self.assertEqual(loaded["text"], self.document["text"])
        self.assertEqual(loaded["pages"][1]["page_number"], 2)
        self.assertFalse(loaded["stale"])
        self.assertEqual(len(reopened.load_documents(42, readable_only=True)), 1)

    def test_failed_refresh_keeps_old_text_but_excludes_it_from_readable_content(self):
        self.store.save_document(42, self.document, "first")
        failed = {**self.document, "version": "v2", "status": "unavailable", "text": "", "pages": []}
        self.store.save_document(42, failed, "first")
        self.store.save_document(42, failed, "first")
        loaded = self.store.load_document(42, "file:9")
        self.assertEqual(loaded["text"], self.document["text"])
        self.assertTrue(loaded["stale"])
        self.assertEqual(loaded["latest_attempt"]["status"], "unavailable")
        self.assertEqual(self.store.load_documents(42, readable_only=True), [])

    def test_accounts_and_courses_are_isolated(self):
        self.store.save_document(42, self.document, "first")
        other = IngestionStore("student-b", self.path)
        self.assertIsNone(other.load_document(42, "file:9"))
        self.assertIsNone(other.load_course(42))
        self.assertIsNone(self.store.load_document(43, "file:9"))

    def test_old_records_remain_saved_but_are_not_current_after_a_new_snapshot(self):
        self.store.save_document(42, self.document, "first")
        self.store.save_course({**self.snapshot, "synced_at": "second"})
        self.assertEqual(self.store.load_documents(42), [])
        self.assertIsNotNone(self.store.load_document(42, "file:9"))

    def test_empty_and_missing_course(self):
        self.assertEqual(self.store.load_documents(42), [])
        self.assertEqual(self.store.load_documents(999), [])
