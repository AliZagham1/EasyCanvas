"""EC-012: saved course material stays available while a Canvas sync is unfinished.

Temporary SQLite, mocked Canvas and fake providers only; no API credit is used.
"""

import asyncio
import copy
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import requests

from backend.app import chunking_service, course_sync_service, ingestion_service, visual_document_service
from backend.app.conversation_service import ConversationService
from backend.app.document_quality_service import PREVIOUS_VERSION_MESSAGE, NEWER_VERSION_MESSAGE, answer_limitations
from backend.app.ingestion_store import IngestionStore, visible_in_snapshot
from backend.app.query_understanding_service import empty_understanding
from backend.app.retrieval_router import material_notice
from backend.app.retrieval_service import search_course
from backend.app.search_index_service import index_status, process_jobs
from backend.tests.test_document_service import make_pdf
from backend.tests.test_ingestion_pipeline import snapshot as base_snapshot
from backend.tests.test_query_routing import PlannedProvider
from backend.tests.test_search_index import Provider

class EchoingProvider(PlannedProvider):
    """Like the live answer model in the EC-012 final run: repeats sync wording it is shown."""
    LIMITATION = "One diagram on slide 3 is described only briefly."

    def generate(self, instructions, cid, question, passages):
        result = super().generate(instructions, cid, question, passages)
        echoed = sorted({limitation.replace(". This", "; this") for passage in passages
                         for limitation in passage.get("limitations", []) if "Canvas sync" in limitation})
        result["answer"]["limitations"] = echoed + [self.LIMITATION]
        return result


FIRST, SECOND, THIRD = "2026-11-01T00:00:00+00:00", "2026-11-02T00:00:00+00:00", "2026-11-03T00:00:00+00:00"


def http_error(code):
    return requests.HTTPError(response=Mock(status_code=code))


class SyncGenerationTests(unittest.TestCase):
    def setUp(self):
        patch.dict(os.environ, {"EASYCANVAS_VISION_ENABLED": "false"}).start()
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.store = IngestionStore("student", Path(directory.name) / "generations.sqlite3")
        self.store.save_sync_state({"courses": [{"course_id": 42}], "status": "completed"})
        self.provider = Provider()
        self.snapshot = base_snapshot()
        self.metadata = {"id": 9, "filename": "lecture.pdf", "display_name": "Lecture.pdf",
                         "updated_at": "2026-10-01T00:00:00Z", "size": 1024, "url": "https://canvas.example.edu/files/9/download"}
        self.content = make_pdf(["Linear algebra lecture"])
        self.during_download = None
        patch.object(ingestion_service, "sync_course", side_effect=lambda *a, **k: copy.deepcopy(self.snapshot)).start()
        self.info = patch.object(ingestion_service.canvas_service, "get_file_info",
                                 side_effect=lambda *a: copy.deepcopy(self.metadata)).start()
        self.download = patch.object(ingestion_service.canvas_service, "download_canvas_file", side_effect=self._download).start()
        self.addCleanup(patch.stopall)

    def _download(self, *args, **kwargs):
        if self.during_download:
            self.during_download()
        return kwargs["metadata"], self.content

    def sync(self, synced_at, **changes):
        self.snapshot["synced_at"] = synced_at
        self.snapshot.update(changes)
        return ingestion_service.ingest_course(42, store=self.store)

    def indexed_first_sync(self):
        self.sync(FIRST)
        process_jobs(self.store, provider=self.provider)
        self.provider.calls.clear()
        self.assertEqual(index_status(42, store=self.store)["status"], "ready")

    def change_pdf(self, text="Updated eigenvalue lecture"):
        self.metadata["updated_at"] = "2026-10-05T00:00:00Z"
        self.content = make_pdf([text])

    def search_texts(self, question="lecture"):
        result = search_course(42, question, store=self.store, provider=self.provider, top_k=10)
        return result, [item["text"] for item in result["results"]]

    # -- generation visibility ------------------------------------------------

    def test_published_material_stays_searchable_while_a_sync_is_running(self):
        self.indexed_first_sync()
        self.change_pdf()
        seen = {}

        def inspect():
            course = self.store.load_course(42)
            seen["published"] = course["synced_at"]
            seen["refresh"] = course["refresh"]["status"]
            seen["documents"] = sorted(d["document_key"] for d in self.store.load_documents(42, readable_only=True))
            seen["status"] = index_status(42, store=self.store)["status"]
            seen["search"] = self.search_texts()
        self.during_download = inspect
        self.sync(SECOND)
        self.assertEqual(seen["published"], FIRST)          # not replaced early
        self.assertEqual(seen["refresh"], "running")
        self.assertEqual(seen["documents"], ["assignment:7", "file:9", "syllabus:42"])
        self.assertEqual(seen["status"], "syncing")
        result, texts = seen["search"]
        self.assertEqual(result["status"], "ready")
        self.assertTrue(any("Linear algebra lecture" in text for text in texts))
        course = self.store.load_course(42)
        self.assertEqual(course["synced_at"], SECOND)       # committed only at the end
        self.assertNotIn("refresh", course)
        self.assertEqual(course["ingestion_status"], "finished")

    def test_discovery_phase_is_marked_and_saved_material_stays_available(self):
        self.indexed_first_sync()
        self.change_pdf()
        seen = {}

        def discover(*args, **kwargs):
            course = self.store.load_course(42)
            seen["discovery"] = dict(course["refresh"], published=course["synced_at"], notice=material_notice(course),
                                     docs=len(self.store.load_documents(42, readable_only=True, served=True)),
                                     search=self.search_texts()[0]["status"])
            self.snapshot["synced_at"] = SECOND
            return copy.deepcopy(self.snapshot)

        def processing():
            course = self.store.load_course(42)
            seen["processing"] = dict(course["refresh"], notice=material_notice(course))
        self.during_download = processing
        with patch.object(ingestion_service, "sync_course", side_effect=discover):
            ingestion_service.ingest_course(42, store=self.store)
        discovery = seen["discovery"]
        self.assertEqual((discovery["status"], discovery["phase"], discovery["synced_at"]), ("running", "discovery", None))
        self.assertEqual(discovery["published"], FIRST)
        self.assertIn("last completed sync", discovery["notice"])
        self.assertEqual((discovery["docs"], discovery["search"]), (3, "ready"))
        self.assertEqual((seen["processing"]["phase"], seen["processing"]["synced_at"]), ("processing", SECOND))
        self.assertIn("last completed sync", seen["processing"]["notice"])
        course = self.store.load_course(42)
        self.assertNotIn("refresh", course)
        self.assertIsNone(material_notice(course))

    def test_discovery_failure_keeps_published_material_and_reports_unfinished_sync(self):
        self.indexed_first_sync()
        with patch.object(ingestion_service, "sync_course", side_effect=requests.ConnectionError("dns")):
            with self.assertRaises(requests.ConnectionError):
                ingestion_service.ingest_course(42, store=self.store)
        course = self.store.load_course(42)
        self.assertEqual(course["synced_at"], FIRST)
        self.assertEqual((course["refresh"]["status"], course["refresh"]["phase"], course["refresh"]["error_type"]),
                         ("failed", "discovery", "ConnectionError"))
        self.assertIn("did not finish", material_notice(course))
        self.assertEqual(len(self.store.load_documents(42, readable_only=True, served=True)), 3)
        self.assertEqual(index_status(42, store=self.store)["status"], "ready")
        self.assertEqual(self.search_texts()[0]["status"], "ready")
        self.sync(SECOND)
        self.assertIsNone(material_notice(self.store.load_course(42)))

    def test_first_sync_discovery_failure_saves_nothing(self):
        with patch.object(ingestion_service, "sync_course", side_effect=requests.Timeout()):
            with self.assertRaises(requests.Timeout):
                ingestion_service.ingest_course(42, store=self.store)
        self.assertIsNone(self.store.load_course(42))

    def test_documents_last_seen_before_the_published_sync_stay_excluded(self):
        published = {"synced_at": SECOND, "refresh": {"status": "running", "synced_at": THIRD}}
        self.assertTrue(visible_in_snapshot(SECOND, published))
        self.assertTrue(visible_in_snapshot(THIRD, published))
        self.assertFalse(visible_in_snapshot(FIRST, published))
        self.assertFalse(visible_in_snapshot(THIRD, {"synced_at": SECOND}))  # no pending refresh

    def test_unchanged_sync_does_not_redownload_reextract_rechunk_or_reembed(self):
        self.indexed_first_sync()
        report = self.sync(SECOND)
        self.assertEqual(report["statistics"]["downloads"], 0)
        self.assertEqual(report["statistics"]["processed"], 0)
        self.assertEqual(report["chunking"]["processed"], 0)
        self.assertEqual(process_jobs(self.store, provider=self.provider), [])
        self.assertEqual(self.provider.calls, [])
        with self.store._connection() as c:
            self.assertEqual(c.execute("SELECT count(*) FROM document_served_versions").fetchone()[0], 0)

    def test_served_view_works_on_a_database_without_the_new_table(self):
        # Read-only stores (live evaluation) skip constructor migrations.
        self.indexed_first_sync()
        with self.store._connection() as c:
            c.execute("DROP TABLE document_served_versions")
        self.assertEqual(len(self.store.load_documents(42, readable_only=True, served=True)), 3)
        self.assertEqual(self.search_texts()[0]["status"], "ready")
        self.assertEqual(self.store.prune_served_versions(42), 0)

    # -- changed documents keep their previous searchable version -------------

    def test_changed_document_serves_previous_version_until_replacement_is_embedded(self):
        self.indexed_first_sync()
        self.change_pdf()
        report = self.sync(SECOND)
        self.assertEqual(report["chunking"]["processed"], 1)  # chunked during the sync
        latest = self.store.load_chunks(42, "file:9")
        served = self.store.load_chunks(42, "file:9", served=True)
        self.assertIn("Updated eigenvalue lecture", latest[0]["text"])
        self.assertIn("Linear algebra lecture", served[0]["text"])
        self.assertEqual(served[0]["served_version"], "previous")
        self.assertEqual(served[0]["title"], "Lecture.pdf")
        status = index_status(42, store=self.store)
        self.assertEqual(status["remaining_chunks"], 1)      # truthful indexing progress
        result, texts = self.search_texts()
        self.assertEqual(result["status"], "ready")          # no retrieval gap
        self.assertTrue(any("Linear algebra lecture" in text for text in texts))
        origin = next(o for item in result["results"] for o in item["sources"] if o["document_key"] == "file:9")
        self.assertIn(PREVIOUS_VERSION_MESSAGE, answer_limitations(origin, "explain the lecture"))

        self.provider.calls.clear()
        reports = process_jobs(self.store, provider=self.provider)
        self.assertEqual((reports[0]["generated"], len(self.provider.calls)), (1, 1))  # only the replacement text
        served = self.store.load_chunks(42, "file:9", served=True)
        self.assertIn("Updated eigenvalue lecture", served[0]["text"])
        self.assertIsNone(served[0]["served_version"])
        with self.store._connection() as c:
            self.assertEqual(c.execute("SELECT count(*) FROM document_served_versions").fetchone()[0], 0)

    def test_previous_version_is_never_served_after_access_revocation(self):
        self.indexed_first_sync()
        self.change_pdf()
        self.sync(SECOND)                                    # previous retained, replacement unindexed
        self.assertIn("Linear algebra lecture", self.store.load_chunks(42, "file:9", served=True)[0]["text"])
        self.info.side_effect = http_error(403)
        self.snapshot["file_inventory"][0]["metadata"] = None
        self.sync(THIRD)
        document = self.store.load_document(42, "file:9")
        self.assertEqual(document["cache_blocked"], "access_revoked")
        self.assertEqual(self.store.load_chunks(42, "file:9", served=True), [])
        self.assertEqual(self.store.load_chunks(42, "file:9"), [])
        result, _ = self.search_texts()
        self.assertNotIn("file:9", {o["document_key"] for item in result["results"] for o in item["sources"]})
        with self.store._connection() as c:                  # pruned at commit
            self.assertEqual(c.execute("SELECT count(*) FROM document_served_versions").fetchone()[0], 0)

    def test_access_revocation_during_a_running_sync_hides_the_document_immediately(self):
        self.indexed_first_sync()
        self.info.side_effect = http_error(404)
        self.snapshot["file_inventory"][0]["metadata"] = None
        seen = {}
        original = self.store.save_document

        def save(course_id, document, seen_at):
            original(course_id, document, seen_at)
            if document["document_key"] == "file:9":
                seen["chunks"] = self.store.load_chunks(42, "file:9", served=True)
                seen["refresh"] = self.store.load_course(42)["refresh"]["status"]
        with patch.object(self.store, "save_document", side_effect=save):
            self.sync(SECOND)
        self.assertEqual(seen, {"chunks": [], "refresh": "running"})

    # -- failures ---------------------------------------------------------------

    def test_unexpected_extractor_error_affects_only_that_file_and_keeps_last_good_text(self):
        self.indexed_first_sync()
        self.change_pdf()
        with patch.object(visual_document_service, "extract_file", side_effect=KeyError("bug")):
            report = self.sync(SECOND)
        outcome = next(d for d in report["documents"] if d["document_key"] == "file:9")
        self.assertEqual((outcome["status"], outcome["action"], outcome["error_type"]), ("error", "failed", "KeyError"))
        self.assertEqual(self.store.load_course(42)["ingestion_status"], "finished")
        document = self.store.load_document(42, "file:9")
        self.assertTrue(document["content_usable"])
        self.assertEqual(document["cache_status"], "stale_but_usable")
        self.assertTrue(document["newer_version_unavailable"])
        self.assertEqual(document["latest_attempt"]["error_category"], "processing_error")
        result, texts = self.search_texts()
        self.assertTrue(any("Linear algebra lecture" in text for text in texts))
        origin = next(o for item in result["results"] for o in item["sources"] if o["document_key"] == "file:9")
        self.assertIn(NEWER_VERSION_MESSAGE, answer_limitations(origin, "explain the lecture"))
        # Other documents are processed normally; the next sync retries the file.
        self.assertEqual(len(self.store.load_documents(42, readable_only=True)), 3)
        report = self.sync(THIRD)
        self.assertEqual(report["statistics"]["downloads"], 1)
        self.assertEqual(self.store.load_document(42, "file:9")["cache_status"], "current")

    def test_unexpected_error_on_a_first_seen_file_does_not_stop_the_course(self):
        with patch.object(visual_document_service, "extract_file", side_effect=TypeError("bug")):
            report = self.sync(FIRST)
        self.assertEqual(self.store.load_course(42)["ingestion_status"], "finished")
        self.assertFalse(self.store.load_document(42, "file:9")["content_usable"])
        self.assertEqual(sorted(d["document_key"] for d in self.store.load_documents(42, readable_only=True)),
                         ["assignment:7", "syllabus:42"])
        self.assertEqual(report["statistics"]["error"], 1)

    def test_course_level_failure_keeps_published_generation_then_next_sync_commits(self):
        self.indexed_first_sync()
        self.change_pdf()
        with patch.object(chunking_service, "ensure_course_chunks", side_effect=sqlite3.OperationalError("locked")):
            with self.assertRaises(sqlite3.OperationalError):
                self.sync(SECOND)
        course = self.store.load_course(42)
        self.assertEqual(course["synced_at"], FIRST)
        self.assertEqual((course["refresh"]["status"], course["refresh"]["error_type"]), ("failed", "OperationalError"))
        self.assertEqual(len(self.store.load_documents(42, readable_only=True)), 3)
        self.assertNotEqual(index_status(42, store=self.store)["status"], "syncing")
        result, texts = self.search_texts()
        self.assertEqual(result["status"], "ready")
        self.assertTrue(any("Linear algebra lecture" in text for text in texts))  # previous version still served
        self.assertEqual(self.store.load_index_job(42)["status"], "queued")       # replacement can still be indexed
        self.sync(THIRD)
        course = self.store.load_course(42)
        self.assertEqual(course["synced_at"], THIRD)
        self.assertNotIn("refresh", course)

    def test_document_removed_from_canvas_stays_until_a_sync_commits(self):
        self.indexed_first_sync()
        with patch.object(chunking_service, "ensure_course_chunks", side_effect=RuntimeError("bug")):
            with self.assertRaises(RuntimeError):
                self.sync(SECOND, file_inventory=[])
        self.assertIn("file:9", [d["document_key"] for d in self.store.load_documents(42, readable_only=True)])
        self.sync(THIRD, file_inventory=[])
        self.assertNotIn("file:9", [d["document_key"] for d in self.store.load_documents(42, readable_only=True)])
        self.assertEqual(self.store.load_chunks(42, "file:9", served=True), [])

    # -- interruption and recovery --------------------------------------------

    def test_interrupted_sync_is_recorded_without_hiding_saved_material(self):
        self.indexed_first_sync()
        self.change_pdf()

        def stop():
            raise KeyboardInterrupt
        self.during_download = stop
        with self.assertRaises(KeyboardInterrupt):
            self.sync(SECOND)
        course = self.store.load_course(42)
        self.assertEqual((course["synced_at"], course["refresh"]["status"]), (FIRST, "interrupted"))
        self.assertEqual(len(self.store.load_documents(42, readable_only=True)), 3)
        _, texts = self.search_texts()
        self.assertTrue(any("Linear algebra lecture" in text for text in texts))

    def test_worker_startup_recovers_refreshes_left_running_by_a_killed_process(self):
        self.indexed_first_sync()
        course = self.store.load_course(42)
        self.store.save_course({**course, "refresh": {"status": "running", "synced_at": SECOND}})
        self.store.save_course({"course_id": 43, "synced_at": FIRST, "ingestion_status": "running"})
        report = {"courses": [], "total_courses": 0, "completed_courses": 0, "failed_courses": 0, "coverage": "ok"}
        with patch.object(course_sync_service, "ingest_current_courses", return_value=report), \
                patch.object(course_sync_service.time, "sleep", side_effect=KeyboardInterrupt):
            course_sync_service.run_worker(self.store, 60)
        self.assertEqual(self.store.load_course(42)["refresh"]["status"], "interrupted")
        self.assertEqual(self.store.load_course(43)["ingestion_status"], "interrupted")
        self.assertEqual(len(self.store.load_documents(42, readable_only=True)), 3)

    # -- the Study Assistant keeps answering --------------------------------------

    def test_sync_notice_is_not_sent_to_the_answer_model_and_appears_once(self):
        # Reproduces the EC-012 final-run defect: the notice reached passage
        # limitations, the model echoed it with different punctuation, and the
        # near-duplicate survived exact-match de-duplication.
        self.indexed_first_sync()
        with self.store._connection() as c:  # make the index partial: a normal limitation must survive
            missing = c.execute("SELECT text_hash FROM chunk_embeddings LIMIT 1").fetchone()[0]
            c.execute("DELETE FROM chunk_embeddings WHERE text_hash=?", (missing,))
        course = self.store.load_course(42)
        self.store.save_course({**course, "refresh": {"status": "running", "phase": "processing", "synced_at": SECOND}})
        question = "What does the lecture cover?"
        llm = EchoingProvider(empty_understanding(question))
        sessions = ConversationService()

        async def ask():
            session = sessions.acquire("student", 42)
            return [event async for event in sessions.events(session, question, "turn-1", store=self.store,
                                                              provider=llm, embedding_provider=self.provider)]
        final = asyncio.run(ask())[-1]["data"]
        self.assertEqual(final["status"], "answered")
        passages = llm.answer_calls[0][3]
        sent = [limitation for passage in passages for limitation in passage["limitations"]]
        self.assertFalse(any("Canvas sync" in limitation for limitation in sent))
        self.assertIn("The course index is incomplete; answers use only indexed material.", sent)
        self.assertEqual([w for w in final["warnings"] if "canvas sync" in w.casefold()], [final["material_notice"]])
        self.assertIn("The course index is incomplete; answers use only indexed material.", final["warnings"])
        self.assertIn(EchoingProvider.LIMITATION, final["warnings"])
        self.assertTrue(final["sources"])

    def test_assistant_answers_from_saved_material_during_a_sync_with_a_notice(self):
        self.indexed_first_sync()
        course = self.store.load_course(42)
        # Discovery phase: no new generation yet, but the answer must say a sync is running.
        self.store.save_course({**course, "refresh": {"status": "running", "phase": "discovery", "synced_at": None}})
        question = "What does the lecture cover?"
        llm = PlannedProvider(empty_understanding(question))
        sessions = ConversationService()

        async def ask():
            session = sessions.acquire("student", 42)
            return [event async for event in sessions.events(session, question, "turn-1", store=self.store,
                                                              provider=llm, embedding_provider=self.provider)]
        final = asyncio.run(ask())[-1]["data"]
        self.assertEqual(final["status"], "answered")
        self.assertEqual(final["retrieval_route"], "course_material")
        self.assertTrue(final["sources"])
        self.assertTrue(any("last completed sync" in warning for warning in final["warnings"]))
        self.assertIn("last completed sync", final["material_notice"])  # shown outside the limitations list
        self.assertEqual(len(llm.answer_calls), 1)
        self.assertEqual(self.provider.calls, [[question]])  # only the question; no indexing during chat


if __name__ == "__main__":
    unittest.main()


import backend.tests.ai_consent_granted  # noqa: E402,F401  (AI tests use fake providers)
