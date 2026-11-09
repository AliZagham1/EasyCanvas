import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from backend.app import chunking_service as chunking
from backend.app.ingestion_store import IngestionStore


def document(**changes):
    return {
        "course_id": 42, "document_key": "file:9", "canvas_id": 9,
        "title": "Lecture.pdf", "source_type": "file", "format": "pdf",
        "url": "https://canvas.example/courses/42/files/9", "status": "ok",
        "version": "v1", "text": "Lecture content", "pages": [],
        **changes,
    }


class ChunkSplittingTests(unittest.TestCase):
    def test_short_text_and_source_offsets(self):
        chunks = chunking.chunk_document(document(text="Read this policy."))
        self.assertEqual(chunks, [{
            "chunk_index": 0, "text": "Read this policy.",
            "locations": [{"kind": "text", "start_character": 0, "end_character": 17}],
        }])

    def test_long_text_overlap_bounds_and_complete_coverage(self):
        text = "Paragraph about scheduling. " * 250
        chunks = chunking.chunk_document(document(text=text))
        covered = set()
        previous = None
        for index, chunk in enumerate(chunks):
            location = chunk["locations"][0]
            start, end = location["start_character"], location["end_character"]
            self.assertEqual(chunk["chunk_index"], index)
            self.assertEqual(chunk["text"], text[start:end])
            self.assertLessEqual(len(chunk["text"]), 2000)
            covered.update(range(start, end))
            if previous:
                overlap = previous["end_character"] - start
                self.assertGreater(overlap, 0)
                self.assertLessEqual(overlap, 200)
                self.assertGreater(start, previous["start_character"])
                self.assertEqual(chunk["text"][:overlap], text[start:previous["end_character"]])
            previous = location
        self.assertEqual(covered, set(range(len(text))))

    def test_paragraph_boundary_is_preferred(self):
        text = "A" * 1200 + "\n\n" + "B" * 1200
        chunks = chunking.chunk_document(document(text=text))
        self.assertEqual(chunks[0]["text"], "A" * 1200 + "\n\n")

    def test_overlap_moves_forward_to_clean_boundaries_without_losing_text(self):
        text = ("Meaningful_explanation123 about scheduling and processes.\n" * 150)
        chunks = chunking.chunk_document(document(text=text))
        covered = set()
        previous_end = None
        for chunk in chunks:
            loc = chunk["locations"][0]
            start, end = loc["start_character"], loc["end_character"]
            self.assertFalse(chunking._inside_word(text, start))
            if previous_end is not None:
                self.assertGreaterEqual(start, previous_end - 200)
                self.assertLessEqual(start, previous_end)
            self.assertLessEqual(len(chunk["text"]), 2000)
            covered.update(range(start, end))
            previous_end = end
        self.assertEqual(covered, set(range(len(text))))

    def test_overlap_uses_zero_overlap_when_only_clean_boundary_is_at_end(self):
        text = "A" * 1900 + " " + "B" * 2200
        spans = list(chunking._text_spans(text))
        self.assertEqual(spans[0][1], 1901)
        self.assertFalse(chunking._inside_word(text, spans[1][0]))

    def test_meaningful_short_text_is_eligible_without_a_length_cutoff(self):
        for text in ("x = 2", "E = mc²", "∑ xᵢ", "1 0\n0 1", "ls -la", "cat notes.txt", "Deadlock prevention", "Reduced Echelon Form", "Setup"):
            with self.subTest(text=text):
                doc = document(text=text)
                chunk = chunking.chunk_document(doc)[0]
                self.assertTrue(chunking.chunk_metadata(doc, chunk)["embedding_eligible"])

    def test_obvious_low_information_is_flagged_without_removing_chunks(self):
        cases = (("25", "page_number_only"), ("Page 2 of 5", "page_number_only"),
                 ("HW3.pdf", "filename_only"), ("The end", "boilerplate_label_only"))
        for text, reason in cases:
            with self.subTest(text=text):
                doc = document(text=text, pages=[{"page_number": 1, "status": "ok", "text": text}])
                chunks = chunking.chunk_document(doc)
                self.assertEqual(chunks[0]["text"], text)
                metadata = chunking.chunk_metadata(doc, chunks[0])
                self.assertFalse(metadata["embedding_eligible"])
                self.assertEqual(metadata["eligibility_reason"], reason)

    def test_repeated_document_title_is_flagged_but_distinct_short_heading_is_not(self):
        pages = [{"page_number": n, "text": "Disks", "status": "ok"} for n in (1, 2, 3)]
        pages.append({"page_number": 4, "text": "Disk scheduling", "status": "ok"})
        doc = document(title="Disks.pdf", text="Combined", pages=pages)
        chunks = chunking.chunk_document(doc)
        self.assertEqual(chunking.chunk_metadata(doc, chunks[0])["eligibility_reason"], "repeated_document_title_only")
        self.assertTrue(chunking.chunk_metadata(doc, chunks[-1])["embedding_eligible"])

    def test_code_cells_and_code_files_are_exempt_from_label_filtering(self):
        code_chunk = {"text": "end", "locations": [{"kind": "notebook_cell", "cell_type": "code"}]}
        self.assertTrue(chunking.chunk_metadata(document(), code_chunk)["embedding_eligible"])
        plain_chunk = {"text": "end", "locations": [{"kind": "text"}]}
        self.assertTrue(chunking.chunk_metadata(document(title="example.m"), plain_chunk)["embedding_eligible"])

    def test_template_guidance_is_flagged_but_normal_policy_mentions_are_not(self):
        cases = (
            ("syllabus", "First, verify your syllabus uses the most recent version of the Provost's Syllabus Template."),
            ("page", "This page should remain unpublished until you review the following: Edit this policy."),
            ("page", "Please review the content of this section before publishing this page."),
        )
        for kind, text in cases:
            quality = chunking.source_quality(document(source_type=kind, text=text))
            self.assertIn("instructor_template_guidance", quality["flags"])
            self.assertFalse(quality["policy_retrieval_eligible"])
        for text in ("Late assignments lose 10% per day.", "We use a syllabus template to organize course information."):
            self.assertTrue(chunking.source_quality(document(source_type="syllabus", text=text))["policy_retrieval_eligible"])
        self.assertEqual(chunking.source_quality(document(text=cases[0][1]))["flags"], [])

    def test_unbroken_unicode_text_and_exact_limit(self):
        for length in (2000, 2001, 8000):
            text = "界" * length
            chunks = chunking.chunk_document(document(text=text))
            self.assertEqual(len(chunks), {2000: 1, 2001: 2, 8000: 5}[length])
            self.assertEqual(chunks[-1]["locations"][0]["end_character"], length)
            self.assertTrue(all(0 < len(chunk["text"]) <= 2000 for chunk in chunks))

    def test_unreadable_stale_empty_and_whitespace_are_skipped(self):
        for changes in ({"status": "error"}, {"status": "no_text"}, {"status": "unsupported"}, {"stale": True}, {"text": ""}, {"text": " \n\t"}):
            self.assertEqual(chunking.chunk_document(document(**changes)), [])

    def test_pdf_pages_remain_separate_and_original_numbers_survive(self):
        pages = [
            {"page_number": 1, "text": "First " * 800, "status": "ok"},
            {"page_number": 2, "text": "", "status": "no_text"},
            {"page_number": 3, "text": "Third page", "status": "ok"},
        ]
        chunks = chunking.chunk_document(document(text="Combined", pages=pages, status="partial"))
        self.assertEqual([chunk["locations"][0]["page_number"] for chunk in chunks][-1], 3)
        self.assertTrue(all(len(chunk["locations"]) == 1 for chunk in chunks))
        self.assertNotIn(2, [chunk["locations"][0]["page_number"] for chunk in chunks])
        for chunk in chunks:
            location = chunk["locations"][0]
            self.assertEqual(chunk["text"], pages[location["page_number"] - 1]["text"][location["start_character"]:location["end_character"]])

    def test_slides_keep_titles_notes_offsets_and_warnings(self):
        slides = [
            {"slide_number": 2, "title": "Scheduling", "text": "Slide text\n\nSpeaker notes:\nDiscussion", "notes_start_character": 27, "status": "partial", "warnings": ["images_not_extracted"]},
            {"slide_number": 3, "text": "Next slide", "status": "ok"},
        ]
        chunks = chunking.chunk_document(document(text="Combined", slides=slides, status="partial", format="pptx"))
        self.assertEqual(len(chunks), 2)
        self.assertEqual(chunks[0]["locations"][0]["slide_number"], 2)
        self.assertEqual(chunks[0]["locations"][0]["notes_start_character"], 27)
        self.assertEqual(chunks[0]["locations"][0]["title"], "Scheduling")
        self.assertIn("images_not_extracted", chunks[0]["locations"][0]["warnings"])

    def test_long_slides_split_without_crossing_slide_boundaries(self):
        slides = [{"slide_number": n, "text": str(n) * 3000, "status": "ok"} for n in (1, 2)]
        chunks = chunking.chunk_document(document(text="Combined", slides=slides))
        self.assertEqual([chunk["locations"][0]["slide_number"] for chunk in chunks], [1, 1, 2, 2])

    def test_word_blocks_group_by_location_and_keep_all_references(self):
        blocks = [
            {"block_number": 1, "location": "body", "kind": "paragraph", "text": "Read", "status": "ok"},
            {"block_number": 2, "location": "body", "kind": "table", "text": "A\tB", "status": "ok"},
            {"block_number": 1, "location": "section 1 header", "kind": "paragraph", "text": "Header", "status": "ok"},
        ]
        chunks = chunking.chunk_document(document(text="Combined", blocks=blocks, format="docx"))
        self.assertEqual([chunk["text"] for chunk in chunks], ["Read\n\nA\tB", "Header"])
        self.assertEqual([loc["block_number"] for loc in chunks[0]["locations"]], [1, 2])
        self.assertEqual(chunks[0]["locations"][1]["block_kind"], "table")
        self.assertEqual(chunks[1]["locations"][0]["location"], "section 1 header")
        self.assertTrue(all("page_number" not in loc for chunk in chunks for loc in chunk["locations"]))

    def test_oversized_word_block_splits_with_local_offsets(self):
        text = "Policy " * 600
        block = {"block_number": 4, "location": "body", "kind": "paragraph", "text": text, "status": "ok"}
        chunks = chunking.chunk_document(document(text=text, blocks=[block]))
        self.assertGreater(len(chunks), 1)
        for chunk in chunks:
            loc = chunk["locations"][0]
            self.assertEqual(loc["block_number"], 4)
            self.assertEqual(chunk["text"], text[loc["start_character"]:loc["end_character"]])

    def test_notebook_cells_keep_absolute_offsets_and_never_execute(self):
        from backend.app.document_service import extract_notebook
        import json

        payload = {"nbformat": 4, "cells": [
            {"cell_type": "markdown", "source": "Instructions"},
            {"cell_type": "code", "source": "raise RuntimeError('Never execute')\n" * 100},
        ]}
        extracted = extract_notebook(json.dumps(payload).encode())
        chunks = chunking.chunk_document(document(**extracted))
        self.assertEqual(chunks[0]["locations"][0]["cell_number"], 1)
        self.assertGreater(len(chunks), 2)
        for chunk in chunks:
            loc = chunk["locations"][0]
            self.assertEqual(chunk["text"], extracted["text"][loc["start_character"]:loc["end_character"]])
            if loc["cell_number"] == 2:
                self.assertEqual(loc["cell_type"], "code")

    def test_html_and_plain_text_use_same_splitter(self):
        from backend.app.document_service import extract_html

        extracted = extract_html("<h1>Policy</h1><p>Read this.</p>")
        chunks = chunking.chunk_document(document(**extracted, source_type="page"))
        self.assertEqual(chunks[0]["text"], extracted["text"])
        self.assertEqual(chunks[0]["locations"][0]["kind"], "text")


class ChunkStorageTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "ingestion.sqlite3"
        self.store = IngestionStore("student-a", self.path)
        self.snapshot = {"course_id": 42, "synced_at": "first"}
        self.store.save_course(self.snapshot)
        self.doc = document(references=[{"module_id": 5, "module_name": "Week 1"}])
        self.save()

    def save(self):
        self.store.save_document(42, self.doc, self.snapshot["synced_at"])

    def test_saved_documents_are_backfilled_without_canvas_and_survive_reopen(self):
        with patch("backend.app.canvas_service.download_canvas_file") as download, patch("backend.app.canvas_service.get_courses") as courses:
            report = chunking.ensure_course_chunks(42, self.store)
        download.assert_not_called()
        courses.assert_not_called()
        self.assertEqual(report, {"processed": 1, "reused": 0, "chunks": 1})
        chunks = IngestionStore("student-a", self.path).load_chunks(42)
        self.assertEqual(chunks[0]["canvas_id"], 9)
        self.assertEqual(chunks[0]["title"], "Lecture.pdf")
        self.assertEqual(chunks[0]["source_type"], "file")
        self.assertEqual(chunks[0]["references"], self.doc["references"])
        self.assertEqual(chunks[0]["text"], self.doc["text"])

    def test_accounts_and_courses_are_isolated(self):
        chunking.ensure_course_chunks(42, self.store)
        other = IngestionStore("student-b", self.path)
        self.assertIsNone(other.load_chunk_state(42, "file:9"))
        self.assertEqual(other.load_chunks(42), [])
        self.assertEqual(self.store.load_chunks(43), [])
        other.save_course(self.snapshot)
        other.save_document(42, document(text="Other student"), "first")
        chunking.ensure_course_chunks(42, other)
        self.assertEqual(other.load_chunks(42)[0]["text"], "Other student")
        self.assertEqual(self.store.load_chunks(42)[0]["text"], "Lecture content")
        self.store.save_course({"course_id": 43, "synced_at": "first"})
        self.store.save_document(43, document(course_id=43, text="Other course"), "first")
        chunking.ensure_course_chunks(43, self.store)
        self.assertEqual(self.store.load_chunks(43)[0]["text"], "Other course")
        self.assertEqual(self.store.load_chunks(42)[0]["text"], "Lecture content")

    def test_unchanged_documents_reuse_state_without_running_splitter(self):
        chunking.ensure_course_chunks(42, self.store)
        before = self.store.load_chunk_state(42, "file:9")
        with patch.object(chunking, "chunk_document", side_effect=AssertionError("Should reuse")):
            self.assertEqual(chunking.ensure_course_chunks(42, self.store)["reused"], 1)
        self.assertEqual(self.store.load_chunk_state(42, "file:9"), before)

    def test_changed_document_replaces_only_its_chunks_and_removes_leftovers(self):
        self.doc["text"] = "Long lecture " * 600
        self.save()
        self.store.save_document(42, document(document_key="page:1", text="Unchanged"), "first")
        chunking.ensure_course_chunks(42, self.store)
        self.assertGreater(len(self.store.load_chunks(42, "file:9")), 1)
        unchanged = self.store.load_chunk_state(42, "page:1")
        self.doc["text"] = "Updated short lecture"
        self.save()
        self.assertEqual(self.store.load_chunks(42, "file:9"), [])
        report = chunking.ensure_course_chunks(42, self.store)
        self.assertEqual((report["processed"], report["reused"]), (1, 1))
        self.assertEqual(len(self.store.load_chunks(42, "file:9")), 1)
        self.assertEqual(self.store.load_chunks(42, "file:9")[0]["text"], "Updated short lecture")
        self.assertEqual(self.store.load_chunk_state(42, "page:1"), unchanged)

    def test_location_change_rebuilds_even_with_identical_text(self):
        self.doc["pages"] = [{"page_number": 1, "text": self.doc["text"], "status": "ok"}]
        self.save()
        chunking.ensure_course_chunks(42, self.store)
        self.doc["pages"][0]["page_number"] = 4
        self.save()
        self.assertEqual(chunking.ensure_course_chunks(42, self.store)["processed"], 1)
        self.assertEqual(self.store.load_chunks(42)[0]["locations"][0]["page_number"], 4)

    def test_chunker_version_rebuilds_from_saved_text(self):
        chunking.ensure_course_chunks(42, self.store)
        with patch.object(chunking, "CHUNKER_VERSION", "course-chunks-v2"), patch("backend.app.canvas_service.download_canvas_file") as download:
            self.assertEqual(self.store.load_chunks(42), [])
            self.assertEqual(chunking.ensure_course_chunks(42, self.store)["processed"], 1)
            self.assertEqual(self.store.load_chunks(42)[0]["chunker_version"], "course-chunks-v2")
        download.assert_not_called()

    def test_metadata_only_change_reuses_chunks_but_returns_fresh_metadata(self):
        chunking.ensure_course_chunks(42, self.store)
        self.doc.update(title="Renamed lecture", references=[{"module_name": "Week 2"}], updated_at="later")
        self.save()
        self.assertEqual(chunking.ensure_course_chunks(42, self.store)["reused"], 1)
        self.assertEqual(self.store.load_chunks(42)[0]["title"], "Renamed lecture")
        self.assertEqual(self.store.load_chunks(42)[0]["references"], self.doc["references"])

    def test_duplicate_embedding_inputs_keep_every_origin_and_scope(self):
        self.store.save_document(42, document(document_key="file:10", canvas_id=10, title="Other upload.pdf"), "first")
        chunking.ensure_course_chunks(42, self.store)
        inputs = self.store.load_embedding_inputs(42)
        self.assertEqual(len(inputs), 1)
        self.assertEqual({src["document_key"] for src in inputs[0]["sources"]}, {"file:9", "file:10"})
        self.assertEqual({src["canvas_id"] for src in inputs[0]["sources"]}, {9, 10})
        self.assertEqual(inputs[0]["sources"][1]["references"], self.doc["references"])
        other = IngestionStore("student-b", self.path)
        other.save_course(self.snapshot)
        other.save_document(42, self.doc, "first")
        chunking.ensure_course_chunks(42, other)
        other_input = other.load_embedding_inputs(42)[0]
        self.assertEqual(inputs[0]["text_hash"], other_input["text_hash"])
        self.assertNotEqual(inputs[0]["embedding_reuse_key"], other_input["embedding_reuse_key"])
        self.store.save_course({"course_id": 43, "synced_at": "first"})
        self.store.save_document(43, {**self.doc, "course_id": 43}, "first")
        chunking.ensure_course_chunks(43, self.store)
        self.assertNotEqual(inputs[0]["embedding_reuse_key"], self.store.load_embedding_inputs(43)[0]["embedding_reuse_key"])

    def test_exact_text_only_reuse_and_stale_sources_are_not_returned(self):
        self.store.save_document(42, document(document_key="file:10", text="Lecture  content"), "first")
        chunking.ensure_course_chunks(42, self.store)
        self.assertEqual(len(self.store.load_embedding_inputs(42)), 2)
        self.doc.update(status="unavailable", version="v2", text="")
        self.save()
        remaining = self.store.load_embedding_inputs(42)
        self.assertEqual(len(remaining), 1)
        self.assertEqual(remaining[0]["sources"][0]["document_key"], "file:10")

    def test_ineligible_chunks_are_saved_but_excluded_from_embedding_inputs(self):
        self.doc.update(text="25", pages=[{"page_number": 26, "text": "25", "status": "ok"}])
        self.save()
        chunking.ensure_course_chunks(42, self.store)
        reopened = IngestionStore("student-a", self.path)
        chunks = reopened.load_chunks(42)
        self.assertEqual(chunks[0]["text"], "25")
        self.assertFalse(chunks[0]["embedding_eligible"])
        self.assertEqual(reopened.load_chunks(42, eligible_only=True), [])
        self.assertEqual(reopened.load_embedding_inputs(42), [])
        self.assertEqual(reopened.load_document(42, "file:9")["text"], "25")

    def test_saved_syllabus_gets_quality_flag_and_link_without_rechunking(self):
        self.doc.update(document_key="syllabus:42", source_type="syllabus", url=None,
                        text="Verify your syllabus uses the latest syllabus template.")
        self.save()
        with patch("backend.app.canvas_service.CANVAS_URL", "https://canvas.example"):
            chunking.ensure_course_chunks(42, self.store)
            with patch.object(chunking, "chunk_document", side_effect=AssertionError("Should reuse")):
                self.assertEqual(chunking.ensure_course_chunks(42, self.store)["reused"], 2)
            chunk = self.store.load_chunks(42, "syllabus:42")[0]
            self.assertEqual(chunk["url"], "https://canvas.example/courses/42/assignments/syllabus")
            self.assertFalse(chunk["source_quality"]["policy_retrieval_eligible"])
            self.assertTrue(chunk["embedding_eligible"])
            group = next(item for item in self.store.load_embedding_inputs(42) if item["text"] == self.doc["text"])
            self.assertEqual(group["sources"][0]["source_quality"], chunk["source_quality"])

    def test_stale_unavailable_and_unseen_documents_never_return_old_chunks(self):
        chunking.ensure_course_chunks(42, self.store)
        self.doc.update(status="unavailable", version="v2", text="")
        self.save()
        self.assertEqual(self.store.load_chunks(42), [])
        self.assertEqual(chunking.ensure_course_chunks(42, self.store)["processed"], 0)
        self.store.save_course({**self.snapshot, "synced_at": "second"})
        self.assertEqual(self.store.load_chunks(42), [])

    def test_removed_readable_document_is_excluded(self):
        chunking.ensure_course_chunks(42, self.store)
        self.store.save_course({**self.snapshot, "synced_at": "second"})
        self.assertEqual(self.store.load_chunks(42), [])

    def test_partial_documents_keep_quality_warnings(self):
        self.doc.update(status="partial", warnings=["legacy_doc_structure_unverified"])
        self.save()
        chunking.ensure_course_chunks(42, self.store)
        chunk = self.store.load_chunks(42)[0]
        self.assertEqual(chunk["status"], "partial")
        self.assertEqual(chunk["warnings"], self.doc["warnings"])

    def test_failed_replacement_rolls_back_rows_and_state(self):
        chunking.ensure_course_chunks(42, self.store)
        before = self.store.load_chunks(42)
        state = self.store.load_chunk_state(42, "file:9")
        duplicate = {"chunk_index": 0, "text": "Replacement", "locations": []}
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.save_chunks(42, "file:9", "new-hash", "new-version", [duplicate, duplicate])
        self.assertEqual(self.store.load_chunks(42), before)
        self.assertEqual(self.store.load_chunk_state(42, "file:9"), state)

    def test_empty_chunk_set_has_completion_state_and_is_reused(self):
        with patch.object(chunking, "chunk_document", return_value=[]):
            self.assertEqual(chunking.ensure_course_chunks(42, self.store)["chunks"], 0)
        self.assertEqual(self.store.load_chunk_state(42, "file:9")["chunk_count"], 0)
        with patch.object(chunking, "chunk_document", side_effect=AssertionError("Already processed")):
            self.assertEqual(chunking.ensure_course_chunks(42, self.store)["reused"], 1)

    def test_existing_database_gains_chunk_tables_without_losing_documents(self):
        with closing(sqlite3.connect(self.path)) as connection:
            connection.execute("DROP TABLE document_chunks")
            connection.execute("DROP TABLE document_chunk_state")
        reopened = IngestionStore("student-a", self.path)
        self.assertEqual(reopened.load_document(42, "file:9")["text"], self.doc["text"])
        chunking.ensure_course_chunks(42, reopened)
        self.assertEqual(reopened.load_chunks(42)[0]["text"], self.doc["text"])

    def test_invalid_course_ids_and_missing_course(self):
        for value in (True, 0, -1, "42"):
            with self.assertRaises(ValueError):
                chunking.ensure_course_chunks(value, self.store)
        self.assertEqual(chunking.ensure_course_chunks(999, self.store), {"processed": 0, "reused": 0, "chunks": 0})


if __name__ == "__main__":
    unittest.main()
