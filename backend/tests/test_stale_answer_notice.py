"""A retained previous version must be disclosed truthfully, including when automatic updates are off."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from backend.app import rag_service
from backend.app.chunking_service import ensure_course_chunks
from backend.app.document_quality_service import PREVIOUS_VERSION_MESSAGE, PREVIOUS_VERSION_PAUSED_MESSAGE
from backend.app.ingestion_store import IngestionStore
from backend.app.query_understanding_service import empty_understanding
from backend.app.search_index_service import process_jobs, queue_course
from backend.tests.test_rag import FakeLLM
from backend.tests.test_search_index import Provider

OLD, NEW = "Processes are programs currently executing.", "NEW VERSION. Threads share an address space."


class StaleAnswerNoticeTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.store = IngestionStore("student", Path(self.directory.name) / "test.sqlite3")
        self.store.save_course({"course_id": 42, "synced_at": "first"})
        self.store.save_sync_state({"courses": [{"course_id": 42}], "status": "completed"})
        self.provider = Provider()
        self.save(OLD)
        queue_course(42, store=self.store)
        process_jobs(self.store, provider=self.provider)          # version 1 is indexed and searchable

    def save(self, text):
        doc = {"course_id": 42, "document_key": "file:1", "canvas_id": 1, "title": "Lecture", "source_type": "file",
               "text": text, "status": "ok", "version": "1", "pages": []}
        self.store.save_document(42, doc, "first")
        ensure_course_chunks(42, self.store)

    def ask(self):
        llm = FakeLLM({"status": "answered", "limitations": [],
                       "blocks": [{"text": "A process is a program currently executing.", "source_ids": ["S1"]}]})
        with patch("backend.app.rag_service.query_understanding_service.understand_question",
                   new=AsyncMock(return_value=empty_understanding("what is a process"))):
            return rag_service.answer_question(42, "what is a process", store=self.store, llm_provider=llm,
                                               embedding_provider=self.provider)

    def test_changed_file_with_updates_off_answers_from_old_text_and_says_why(self):
        self.store.update_settings({"auto_index": False})
        self.save(NEW)
        queue_course(42, store=self.store)
        self.assertEqual(process_jobs(self.store, provider=self.provider), [])   # nothing was embedded
        result = self.ask()
        self.assertEqual(result["status"], "answered")
        self.assertIn(PREVIOUS_VERSION_PAUSED_MESSAGE, result["warnings"])
        self.assertNotIn(PREVIOUS_VERSION_MESSAGE, result["warnings"])           # no false "still being prepared"
        self.assertIn("Update course search", PREVIOUS_VERSION_PAUSED_MESSAGE)

    def test_changed_file_with_updates_on_keeps_the_being_prepared_message(self):
        self.save(NEW)                                           # auto_index stays on; job queued but not yet processed
        result = self.ask()
        self.assertIn(PREVIOUS_VERSION_MESSAGE, result["warnings"])
        self.assertNotIn(PREVIOUS_VERSION_PAUSED_MESSAGE, result["warnings"])

    def test_turning_updates_on_again_switches_back_to_the_being_prepared_message(self):
        self.store.update_settings({"auto_index": False})
        self.save(NEW)
        self.assertIn(PREVIOUS_VERSION_PAUSED_MESSAGE, self.ask()["warnings"])
        self.store.update_settings({"auto_index": True})
        warnings = self.ask()["warnings"]
        self.assertIn(PREVIOUS_VERSION_MESSAGE, warnings)
        self.assertNotIn(PREVIOUS_VERSION_PAUSED_MESSAGE, warnings)

    def test_manual_update_removes_the_notice_and_serves_the_new_text(self):
        self.store.update_settings({"auto_index": False})
        self.save(NEW)
        queue_course(42, store=self.store, manual=True)
        process_jobs(self.store, provider=self.provider)
        warnings = self.ask()["warnings"]
        self.assertNotIn(PREVIOUS_VERSION_PAUSED_MESSAGE, warnings)
        self.assertNotIn(PREVIOUS_VERSION_MESSAGE, warnings)
        texts = [c["text"] for c in self.store.load_chunks(42, "file:1", served=True)]
        self.assertTrue(any("Threads share" in t for t in texts))

    def test_unchanged_files_get_no_notice_with_updates_off(self):
        self.store.update_settings({"auto_index": False})
        warnings = self.ask()["warnings"]
        self.assertNotIn(PREVIOUS_VERSION_PAUSED_MESSAGE, warnings)
        self.assertNotIn(PREVIOUS_VERSION_MESSAGE, warnings)


if __name__ == "__main__":
    unittest.main()


import backend.tests.ai_consent_granted  # noqa: E402,F401  (AI tests use fake providers)
