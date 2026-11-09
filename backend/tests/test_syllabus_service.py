"""Regression coverage for direct syllabus links, caching, and provenance."""
import copy
import io

import docx
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import requests

from backend.app import syllabus_service as service, ingestion_service
from backend.app.content_discovery import build_syllabus_inventory, valid_syllabus_url
from backend.app.ingestion_store import IngestionStore
from backend.app.chunking_service import ensure_course_chunks, source_quality
from backend.tests.test_document_service import make_pdf
from backend.tests.test_ingestion_pipeline import snapshot

BASE = "https://canvas.example.edu"
URL = "https://oit-ead-canvas-syllabus.s3.amazonaws.com/canvas.example.edu/2026-FALL/42-CSE-1000/syllabus.pdf"


class SyllabusTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.store = IngestionStore("student", Path(self.directory.name) / "test.sqlite3")
        self.snapshot = snapshot()
        self.snapshot["file_inventory"] = []
        self.snapshot["syllabus"]["metadata"]["syllabus_body"] = f'<p>verify your syllabus uses a syllabus template</p><a href="{URL}">Download Syllabus</a>'
        self.content = make_pdf(["Attendance policy: Attend class regularly.", "Late work requires instructor approval."])
        self.fetch = patch.object(service, "fetch_syllabus", return_value=(self.content, {"etag": '"v1"', "last_modified": "date"})).start()
        patch("backend.app.canvas_service.CANVAS_URL", BASE).start()
        self.addCleanup(patch.stopall)
        self.store.save_course(self.snapshot)

    def ingest(self, **kwargs):
        outcomes = list(service.ingest_syllabus_documents(self.snapshot, self.store, **kwargs))
        for document, _, _ in outcomes:
            self.store.save_document(42, document, self.snapshot["synced_at"])
        ensure_course_chunks(42, self.store)
        return outcomes

    def test_discovery_groups_links_and_preserves_syllabus_and_module_provenance(self):
        self.snapshot["module_items"] = [{"source_type": "module_item", "canvas_id": 4, "title": "Syllabus", "url": URL, "metadata": {"external_url": URL}}]
        self.snapshot["syllabus"]["metadata"]["syllabus_body"] += f'<a href="{URL}">Duplicate</a>'
        entries = build_syllabus_inventory(self.snapshot)
        self.assertEqual(len(entries), 1)
        self.assertEqual([r["source_type"] for r in entries[0]["references"]], ["syllabus", "module_item"])

    def test_only_syllabus_html_can_seed_external_downloads(self):
        self.snapshot["syllabus"] = None
        self.snapshot["assignments"][0]["metadata"]["description"] = f'<a href="{URL}">Download</a>'
        self.assertEqual(build_syllabus_inventory(self.snapshot), [])

    def test_url_security_rejects_foreign_hosts_credentials_redirectors_and_other_courses(self):
        self.assertTrue(valid_syllabus_url(URL, 42))
        for url in [URL.replace("https:", "http:"), URL.replace(".com/", ".com.evil/"), URL.replace("42-CSE", "43-CSE"), URL.replace("canvas.example.edu/", "other.example/"), URL + "?token=bad", URL + "#x", URL.replace("syllabus.pdf", "tool.html"), URL.replace("https://", "https://user:pass@"), URL.replace("/syllabus.pdf", "/../syllabus.pdf"), URL.replace("syllabus.pdf", "%2fprivate.pdf"), "https://[broken"]:
            with self.subTest(url=url): self.assertFalse(valid_syllabus_url(url, 42))

    def test_first_ingestion_extracts_chunks_and_preserves_source_role(self):
        document, action, size = self.ingest()[0]
        self.assertEqual(action, "extracted")
        self.assertEqual(size, len(self.content))
        self.assertEqual(document["source_role"], "course_syllabus")
        self.assertIsNone(document["canvas_id"])
        chunks = self.store.load_chunks(42)
        self.assertEqual(len(chunks), 2)
        self.assertEqual(chunks[0]["locations"][0]["page_number"], 1)
        self.assertEqual(chunks[0]["references"][0]["source_type"], "syllabus")
        self.assertEqual(chunks[0]["source_role"], "course_syllabus")
        self.assertTrue(chunks[0]["source_quality"]["policy_retrieval_eligible"])

    def test_docx_syllabus_preserves_word_block_locations(self):
        word = docx.Document()
        word.add_paragraph("Attendance is required for scheduled class activities.")
        content = io.BytesIO()
        word.save(content)
        docx_url = URL.replace(".pdf", ".docx")
        self.snapshot["syllabus"]["metadata"]["syllabus_body"] = f'<a href="{docx_url}">Download Syllabus</a>'
        self.fetch.return_value = (content.getvalue(), {"etag": '"word-v1"'})
        document = self.ingest()[0][0]
        self.assertEqual(document["format"], "docx")
        self.assertEqual(self.store.load_chunks(42)[0]["locations"][0]["block_number"], 1)

    def test_304_reuses_text_chunks_and_passes_validators(self):
        self.ingest()
        state = self.store.load_chunk_state(42, self.store.load_documents(42)[0]["document_key"])
        self.fetch.return_value = (None, {"etag": '"v1"'})
        with patch.object(service.document_service, "extract_file", side_effect=AssertionError("must reuse")):
            document, action, size = self.ingest()[0]
        self.assertEqual(action, "reused")
        self.assertEqual(size, 0)
        self.assertIn("Attendance", document["text"])
        self.assertEqual(self.fetch.call_args.args[2]["etag"], '"v1"')
        self.assertEqual(self.store.load_chunk_state(42, document["document_key"])["processed_at"], state["processed_at"])

    def test_identical_bytes_reuse_current_canvas_file_without_duplicate_chunks(self):
        document = {"document_key": "file:9", "canvas_id": 9, "source_type": "file", "title": "existing.pdf", "course_id": 42,
                    "pipeline_version": service.document_service.file_pipeline_version({"filename": "existing.pdf"}), "version": "remote", "references": []}
        document.update(service.document_service.extract_file({"filename": "existing.pdf"}, self.content))
        self.store.save_document(42, document, self.snapshot["synced_at"])
        alias, action, _ = self.ingest()[0]
        self.assertEqual(alias["canonical_document_key"], "file:9")
        self.assertEqual(action, "reused")
        self.assertEqual(len(self.store.load_documents(42, readable_only=True)), 1)
        chunks = self.store.load_chunks(42)
        self.assertEqual(len(chunks), 2)
        self.assertEqual(chunks[0]["document_key"], "file:9")
        self.assertEqual(chunks[0]["references"][0]["source_type"], "syllabus")
        self.fetch.return_value = (None, {"etag": '"v1"'})
        self.assertEqual(self.ingest()[0][2], 0)
        self.assertEqual(len(self.store.load_chunks(42)), 2)

    def test_alias_provenance_does_not_require_new_embeddings(self):
        from backend.tests.test_embeddings import FakeProvider
        from backend.app.embedding_service import embed_course
        self.test_identical_bytes_reuse_current_canvas_file_without_duplicate_chunks()
        provider = FakeProvider()
        self.assertEqual(embed_course(42, store=self.store, provider=provider)["generated"], 2)
        self.ingest()
        self.assertEqual(embed_course(42, store=self.store, provider=provider)["generated"], 0)
        self.assertEqual(self.store.load_embedding_inputs(42)[0]["sources"][0]["source_role"], "course_syllabus")

    def test_changed_external_document_replaces_only_its_chunks(self):
        old = self.ingest()[0][0]
        self.fetch.return_value = (make_pdf(["New attendance policy applies to this course."]), {"etag": '"v2"'})
        new = self.ingest()[0][0]
        self.assertEqual(old["document_key"], new["document_key"])
        self.assertNotEqual(old["content_hash"], new["content_hash"])
        self.assertEqual(len(self.store.load_chunks(42)), 1)
        self.assertIn("New attendance", self.store.load_chunks(42)[0]["text"])

    def test_alias_becomes_independent_when_external_bytes_change(self):
        self.test_identical_bytes_reuse_current_canvas_file_without_duplicate_chunks()
        self.fetch.return_value = (make_pdf(["Updated external syllabus policy."]), {"etag": '"v2"'})
        new = self.ingest()[0][0]
        self.assertNotIn("canonical_document_key", new)
        self.assertIn("Updated external", new["text"])
        self.assertEqual(len(self.store.load_documents(42, readable_only=True)), 2)

    def test_removed_alias_target_requires_full_download(self):
        self.test_identical_bytes_reuse_current_canvas_file_without_duplicate_chunks()
        self.snapshot["synced_at"] = "next"
        self.store.save_course(self.snapshot)
        self.fetch.return_value = (self.content, {"etag": '"v1"'})
        new = self.ingest()[0][0]
        self.assertIsNone(self.fetch.call_args.args[2])
        self.assertIn("Attendance", new["text"])
        self.assertNotIn("canonical_document_key", new)

    def test_failures_keep_old_text_but_exclude_stale_chunks(self):
        old = self.ingest()[0][0]
        response = Mock(status_code=403)
        self.fetch.side_effect = requests.HTTPError(response=response)
        new, action, _ = self.ingest()[0]
        self.assertEqual(new["status"], "unavailable")
        self.assertEqual(action, "failed")
        self.assertTrue(self.store.load_document(42, old["document_key"])["stale"])
        self.assertEqual(self.store.load_chunks(42), [])

    def test_cached_embedding_reuse_and_rag_provenance(self):
        from backend.tests.test_embeddings import FakeProvider
        from backend.app.embedding_service import embed_course
        from backend.app.retrieval_service import search_course
        self.ingest()
        provider = FakeProvider()
        first = embed_course(42, store=self.store, provider=provider)
        self.assertEqual(first["generated"], 2)
        self.fetch.return_value = (None, {"etag": '"v1"'})
        self.ingest()
        second = embed_course(42, store=self.store, provider=provider)
        self.assertEqual(second["generated"], 0)
        result = search_course(42, "Attendance policy?", store=self.store, provider=provider)
        source = result["results"][0]["sources"][0]
        self.assertEqual(source["source_role"], "course_syllabus")
        self.assertEqual(source["references"][0]["download_url"], URL)
        self.assertEqual(result["status"], "ready")

    def test_unchanged_bytes_reuse_even_without_http_validators(self):
        self.fetch.return_value = (self.content, {})
        self.ingest()
        with patch.object(service.document_service, "extract_file", side_effect=AssertionError("must reuse")):
            self.assertEqual(self.ingest()[0][1], "reused")

    def test_limit_defers_download(self):
        document, action, _ = self.ingest(max_files=0)[0]
        self.assertEqual(action, "deferred")
        self.fetch.assert_not_called()

    def test_account_and_course_isolation(self):
        self.ingest()
        other = IngestionStore("other-student", self.store.path)
        self.assertEqual(other.load_documents(42), [])
        self.assertEqual(self.store.load_chunks(43), [])

    def test_template_record_remains_excluded_not_inherited_by_file(self):
        self.assertFalse(source_quality({"source_type": "syllabus", "text": "verify your syllabus uses the syllabus template"})["policy_retrieval_eligible"])
        self.assertTrue(source_quality(self.ingest()[0][0])["policy_retrieval_eligible"])

    def test_existing_ingestion_calls_syllabus_stage_before_chunking(self):
        with patch.object(ingestion_service, "sync_course", return_value=copy.deepcopy(self.snapshot)):
            result = ingestion_service.ingest_course(42, store=self.store)
        self.assertEqual(result["statistics"]["downloads"], 1)
        self.assertTrue(any(doc["source_type"] == "syllabus_file" for doc in result["documents"]))
        self.assertTrue(self.store.load_chunks(42))


class SyllabusDownloadTests(unittest.TestCase):
    def setUp(self):
        patch("backend.app.canvas_service.CANVAS_URL", BASE).start()
        self.addCleanup(patch.stopall)
        self.response = Mock(status_code=200, headers={"ETag": '"v1"'})
        self.response.iter_content.return_value = [b"%PDF-1.4 example"]
        self.session = Mock()
        self.session.get.return_value.__enter__ = Mock(return_value=self.response)
        self.session.get.return_value.__exit__ = Mock(return_value=False)
        self.factory = patch.object(service.requests, "Session").start()
        self.factory.return_value.__enter__.return_value = self.session

    def test_no_authentication_environment_credentials_or_redirects(self):
        body, validators = service.fetch_syllabus(URL, 42)
        self.assertTrue(body.startswith(b"%PDF-"))
        self.assertEqual(self.session.trust_env, False)
        kwargs = self.session.get.call_args.kwargs
        self.assertEqual(kwargs["headers"], {})
        self.assertFalse(kwargs["allow_redirects"])
        self.assertTrue(kwargs["stream"])
        self.assertEqual(validators["etag"], '"v1"')

    def test_conditional_get_304_does_not_read_bytes(self):
        self.response.status_code = 304
        body, _ = service.fetch_syllabus(URL, 42, {"etag": '"v1"'})
        self.assertIsNone(body)
        self.response.iter_content.assert_not_called()
        self.assertEqual(self.session.get.call_args.kwargs["headers"], {"If-None-Match": '"v1"'})

    def test_redirect_and_unsolicited_304_are_rejected(self):
        for status in (302, 304):
            self.response.status_code = status
            with self.assertRaises(ValueError):service.fetch_syllabus(URL, 42)

    def test_invalid_url_never_contacts_network(self):
        with self.assertRaises(ValueError):service.fetch_syllabus('https://evil.example/syllabus.pdf', 42)
        self.factory.assert_not_called()

    def test_limits_headers_and_streamed_bytes(self):
        self.response.headers = {"Content-Length": str(service.MAX_BYTES + 1)}
        with self.assertRaises(ValueError):service.fetch_syllabus(URL, 42)
        self.response.headers = {}
        with patch.object(service, "MAX_BYTES", 4):
            with self.assertRaises(ValueError):service.fetch_syllabus(URL, 42)

    def test_html_masquerading_as_document_is_rejected(self):
        self.response.iter_content.return_value = [b"<html>Please sign in</html>"]
        with self.assertRaises(ValueError):service.fetch_syllabus(URL, 42)
