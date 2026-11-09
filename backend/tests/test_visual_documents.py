"""Real PDF objects and fake providers exercise visual ingestion without API spend."""

import copy
import hashlib
import io
import json
import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from zipfile import ZipFile
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pypdfium2 as pdfium
from PIL import Image, ImageDraw
from pypdf import PdfReader, PdfWriter
from pypdf.generic import DictionaryObject, NameObject, NumberObject, DecodedStreamObject

from backend.app import document_service, visual_document_service, legacy_office
from backend.app.chunking_service import ensure_course_chunks, chunk_input_hash
from backend.app.embedding_service import embed_course
from backend.app.ingestion_store import IngestionStore
from backend.app.retrieval_service import resolve_documents, retrieve_evidence, search_course
from backend.app.visual_inspection import inspect_pdf, InspectionSettings, _coverage
from backend.app.vision_service import (
    VisionConfiguration, VisionProviderError, OpenAIVisionExtractionProvider, validate_extraction,
)
from backend.tests.test_document_service import make_pdf
from backend.tests.test_embeddings import FakeProvider
from backend.tests.test_office_documents import save_bytes, image_bytes, make_pptx
from backend.tests import test_ingestion_pipeline as pipeline_fixtures


def visual_pdf(texts, *, image=True, logo=False, vectors=False):
    """Small real PDF with selectable text and image/vector content on each page."""
    reader = PdfReader(io.BytesIO(make_pdf(texts)))
    writer = PdfWriter()
    for source in reader.pages:
        page = writer.add_page(source)
        original = page.get_contents()
        data = original.get_data() if original is not None else b""
        if image:
            pixels = Image.new("RGB", (120, 80), "white")
            ImageDraw.Draw(pixels).text((5, 5), "Question 1: x + y = 7", fill="black")
            obj = DecodedStreamObject()
            obj.set_data(pixels.tobytes())
            obj.update({NameObject("/Type"): NameObject("/XObject"), NameObject("/Subtype"): NameObject("/Image"),
                        NameObject("/Width"): NumberObject(120), NameObject("/Height"): NumberObject(80),
                        NameObject("/ColorSpace"): NameObject("/DeviceRGB"), NameObject("/BitsPerComponent"): NumberObject(8)})
            resources = page.get("/Resources") or DictionaryObject()
            resources[NameObject("/XObject")] = DictionaryObject({NameObject("/Im1"): writer._add_object(obj)})
            page[NameObject("/Resources")] = resources
            data += b"\nq 10 0 0 10 20 280 cm /Im1 Do Q" if logo else b"\nq 240 0 0 120 20 20 cm /Im1 Do Q"
        if vectors:
            data += b"\n" + b"\n".join(f"{10+i*15} 30 10 120 re S".encode() for i in range(12))
        stream = DecodedStreamObject(); stream.set_data(data)
        page[NameObject("/Contents")] = writer._add_object(stream)
    output = io.BytesIO(); writer.write(output)
    return output.getvalue()


class FakeVision:
    def __init__(self, configuration=None, fail_on=None, complete=True):
        self.configuration = configuration or VisionConfiguration(model="fake-vision")
        self.calls, self.fail_on, self.complete = [], fail_on, complete

    def extract_page(self, image_bytes, *, native_text):
        self.calls.append((image_bytes, native_text))
        if len(self.calls) == self.fail_on:
            raise VisionProviderError("Fake temporary failure")
        return {"extraction": {"complete": self.complete, "unreadable": [] if self.complete else ["One matrix entry is unreadable."],
                               "elements": [{"type": "text", "text": native_text or "Worksheet"},
                                            {"type": "equation", "text": "Question 1: Find x and y given x + y = 7."}]},
                "usage": {"input_tokens": 100, "output_tokens": 30}}


class InspectionTests(unittest.TestCase):
    def inspect(self, content):
        native = document_service.extract_pdf(content)
        with pdfium.PdfDocument(content) as pdf:
            return inspect_pdf(pdf, native["pages"])

    def test_heading_and_image_is_not_mistaken_for_complete_text(self):
        inspection = self.inspect(visual_pdf(["Question 1"]))[0]
        self.assertTrue(inspection.needs_vision)
        self.assertEqual(inspection.image_count, 1)
        self.assertTrue(inspection.image_bounds)
        self.assertGreater(inspection.image_area_ratio, .1)

    def test_substantial_native_text_does_not_hide_an_image(self):
        inspection = self.inspect(visual_pdf(["Readable explanation " * 40]))[0]
        self.assertGreater(inspection.native_text_length, 200)
        self.assertTrue(inspection.needs_vision)

    def test_scanned_page_is_detected(self):
        inspection = self.inspect(visual_pdf([None]))[0]
        self.assertTrue(inspection.needs_vision)
        self.assertFalse(inspection.blank_page)
        with pdfium.PdfDocument(visual_pdf([None])) as pdf:
            full = inspect_pdf(pdf, [{"text": ""}], InspectionSettings(scanned_image_ratio=.2))[0]
        self.assertTrue(full.scanned_page)

    def test_native_only_and_blank_pages_do_not_need_vision(self):
        first, blank = self.inspect(make_pdf(["Normal lecture explanation", None]))
        self.assertFalse(first.needs_vision)
        self.assertTrue(blank.blank_page)
        self.assertFalse(blank.needs_vision)

    def test_repeated_tiny_marginal_logo_is_skipped_only_on_text_rich_pages(self):
        inspections = self.inspect(visual_pdf(["Course explanation " * 30] * 3, logo=True))
        self.assertTrue(all(not i.needs_vision and i.decorative_images == 1 for i in inspections))
        self.assertTrue(self.inspect(visual_pdf(["Question 1"], logo=True))[0].needs_vision)

    def test_vectors_without_images_are_detected(self):
        inspection = self.inspect(visual_pdf([None], image=False, vectors=True))[0]
        self.assertTrue(inspection.needs_vision)
        self.assertIn("unread_vector_content", inspection.reasons)

    def test_overlapping_images_are_not_double_counted(self):
        self.assertAlmostEqual(_coverage([(0, 0, 10, 10), (0, 0, 10, 10)], 20, 20), .25)

    def test_corrupt_native_text_is_flagged(self):
        with pdfium.PdfDocument(make_pdf(["Readable"])) as pdf:
            inspection = inspect_pdf(pdf, [{"text": "\ufffd\ufffd\ufffd"}])[0]
        self.assertTrue(inspection.needs_vision)


class EnrichmentTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "ingestion.sqlite3"
        self.store = IngestionStore("student", self.path)
        self.store.save_course({"course_id": 42, "synced_at": "now"})
        self.provider = FakeVision()
        self.content = visual_pdf(["Question 1"])

    def extract(self, content=None, **kwargs):
        return visual_document_service.extract_file({"filename": "Homework.pdf"}, content or self.content,
                                                   store=self.store, course_id=42,
                                                   provider=kwargs.pop("provider", self.provider), **kwargs)

    def test_visual_text_replaces_page_without_duplicate_native_paragraphs(self):
        result = self.extract()
        self.assertEqual(result["status"], "ok")
        self.assertIn("x + y = 7", result["text"])
        self.assertEqual(result["pages"][0]["native_text"], "Question 1")
        self.assertEqual(result["pages"][0]["extraction_method"], "vision")
        self.assertEqual(len(self.provider.calls), 1)
        self.assertEqual(result["text"].count("Find x and y"), 1)

    def test_disabled_vision_preserves_native_text_and_marks_partial(self):
        result = self.extract(provider=None, enabled=False)
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["pages"][0]["extraction_status"], "vision_pending")
        self.assertIn("visual_content_not_extracted", result["pages"][0]["warnings"])
        self.assertIn("Question 1", result["text"])

    def test_text_only_and_blank_pages_never_reach_provider(self):
        result = self.extract(make_pdf(["Readable", None]))
        self.assertEqual(result["status"], "ok")
        self.assertEqual(self.provider.calls, [])
        self.assertEqual(result["pages"][1]["extraction_status"], "blank")

    def test_cache_survives_restart_and_disabled_paid_processing(self):
        first = self.extract()
        self.store = IngestionStore("student", self.path)
        second = self.extract(enabled=False)
        self.assertEqual(first["text"], second["text"])
        self.assertEqual(second["visual_processing"]["reused"], 1)
        self.assertEqual(len(self.provider.calls), 1)

    def test_detection_threshold_change_reuses_compatible_page_transcription(self):
        self.extract(settings=InspectionSettings(drawing_count=8))
        second = self.extract(settings=InspectionSettings(drawing_count=12))
        self.assertEqual(second["visual_processing"]["reused"], 1)
        self.assertEqual(len(self.provider.calls), 1)

    def test_failed_page_is_retried_while_successful_pages_are_reused(self):
        provider = FakeVision(fail_on=2)
        content = visual_pdf(["Page one", "Page two", "Page three"])
        first = self.extract(content, provider=provider)
        self.assertEqual(first["status"], "partial")
        self.assertTrue(first["visual_processing"]["retry_needed"])
        second = self.extract(content, provider=provider)
        self.assertEqual(second["status"], "ok")
        self.assertEqual(second["visual_processing"]["reused"], 2)
        self.assertEqual(second["visual_processing"]["generated"], 1)

    def test_unreadable_details_remain_partial(self):
        result = self.extract(provider=FakeVision(complete=False))
        self.assertEqual(result["status"], "partial")
        self.assertIn("matrix entry", result["pages"][0]["unreadable"][0])
        self.assertIn("visual_content_incomplete", result["pages"][0]["warnings"])

    def test_account_course_content_model_and_prompt_changes_do_not_reuse_wrong_cache(self):
        self.extract()
        for change in (replace(self.provider.configuration, model="new-model"),
                       replace(self.provider.configuration, prompt_version="v2")):
            provider = FakeVision(change)
            self.extract(provider=provider)
            self.assertEqual(len(provider.calls), 1)
        for namespace, cid in (("other", 42), ("student", 43)):
            other = IngestionStore(namespace, self.path);provider = FakeVision()
            visual_document_service.extract_file({"filename": "Homework.pdf"}, self.content,
                                                 store=other, course_id=cid, provider=provider)
            self.assertEqual(len(provider.calls), 1)
        self.extract(visual_pdf(["Changed heading"]))
        self.assertEqual(len(self.provider.calls), 2)

    def test_storage_chunking_and_embeddings_preserve_identity_and_reuse(self):
        extracted = self.extract()
        doc = {**extracted, "document_key": "file:7", "course_id": 42, "canvas_id": 7,
               "source_type": "file", "title": "Homework.pdf", "version": "one",
               "metadata": {"filename": "original-name.pdf"}, "url": "https://canvas.example/courses/42/files/7"}
        self.store.save_document(42, doc, "now")
        ensure_course_chunks(42, self.store)
        chunk = self.store.load_chunks(42)[0]
        self.assertEqual(chunk["locations"][0]["page_number"], 1)
        self.assertEqual(chunk["locations"][0]["extraction_method"], "vision")
        self.assertEqual(chunk["original_filename"], "original-name.pdf")
        self.assertEqual(chunk["content_hash"], hashlib.sha256(self.content).hexdigest())
        self.assertNotIn("native_text", chunk["locations"][0])
        provider = FakeProvider();embed_course(42, store=self.store, provider=provider)
        calls = len(provider.calls)
        again = self.extract()
        self.store.save_document(42, {**doc, **again, "title": "Renamed.pdf"}, "now")
        self.assertEqual(ensure_course_chunks(42, self.store)["reused"], 1)
        embed_course(42, store=self.store, provider=provider)
        self.assertEqual(len(provider.calls), calls)

    def test_original_fingerprints_without_rendered_pages_are_unchanged(self):
        doc = {"text": "Old saved text", "pages": [], "slides": [], "blocks": [], "cells": []}
        expected = hashlib.sha256(json.dumps(doc, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        self.assertEqual(chunk_input_hash(doc), expected)

    def test_rendered_word_page_numbers_stay_ineligible_but_short_equations_remain(self):
        self.store.save_document(42, {"document_key": "file:9", "source_type": "file", "title": "Homework.docx",
            "status": "ok", "pages": [], "rendered_pages": [
                {"rendered_page_number": 1, "text": "1", "status": "ok"},
                {"rendered_page_number": 2, "text": "x = 1", "status": "ok"}]}, "now")
        ensure_course_chunks(42, self.store)
        chunks = self.store.load_chunks(42)
        self.assertFalse(chunks[0]["embedding_eligible"])
        self.assertTrue(chunks[1]["embedding_eligible"])


class VisionProviderTests(unittest.TestCase):
    def response(self, extraction=None, status="completed"):
        return SimpleNamespace(status=status, output_text=json.dumps(extraction or {
            "complete": True, "unreadable": [], "elements": [{"type": "code", "text": "return x + 1;"}]}),
            usage=SimpleNamespace(input_tokens=10, output_tokens=8))

    def test_real_boundary_uses_image_input_structured_output_and_no_provider_storage(self):
        provider = OpenAIVisionExtractionProvider(api_key="fake-secret", usage_recorder=False)
        client = Mock();client.responses.create.return_value = self.response()
        with patch("openai.OpenAI") as factory:
            factory.return_value.__enter__.return_value = client
            result = provider.extract_page(b"\x89PNG\r\n\x1a\nfake-image", native_text="Heading")
        payload = client.responses.create.call_args.kwargs
        self.assertFalse(payload["store"])
        self.assertEqual(payload["input"][0]["content"][1]["type"], "input_image")
        self.assertTrue(payload["text"]["format"]["strict"])
        self.assertEqual(result["usage"]["input_tokens"], 10)
        self.assertNotIn("fake-secret", json.dumps(payload))

    def test_incomplete_provider_response_and_invalid_json_are_not_cached_as_success(self):
        provider = OpenAIVisionExtractionProvider(api_key="fake", usage_recorder=False)
        for response in (self.response(status="incomplete"), self.response()):
            if response.status == "completed":response.output_text = "not JSON"
            client = Mock();client.responses.create.return_value = response
            with patch("openai.OpenAI") as factory:
                factory.return_value.__enter__.return_value = client
                with self.assertRaises(VisionProviderError):provider.extract_page(b"\x89PNG\r\n\x1a\nfake", native_text="")

    def test_bad_shapes_and_false_completeness_are_rejected(self):
        for value in ({}, {"complete": True, "unreadable": ["Missing graph"], "elements": []},
                      {"complete": True, "unreadable": [], "elements": [{"type": "invented", "text": "x"}]}):
            with self.assertRaises(VisionProviderError):validate_extraction(value)

    def test_raw_provider_errors_are_not_exposed(self):
        client = Mock();client.responses.create.side_effect = ValueError("private content and sensitive upstream details")
        with patch("openai.OpenAI") as factory:
            factory.return_value.__enter__.return_value = client
            with self.assertRaises(VisionProviderError) as raised:
                OpenAIVisionExtractionProvider(api_key="fake", usage_recorder=False).extract_page(b"\x89PNG\r\n\x1a\nfake", native_text="")
        self.assertNotIn("private", str(raised.exception))


class SavedBackfillTests(unittest.TestCase):
    setUp = pipeline_fixtures.IngestionPipelineTests.setUp
    run_ingestion = pipeline_fixtures.IngestionPipelineTests.run_ingestion

    def test_backfill_uses_saved_identity_preserves_native_sources_then_skips_unchanged_files(self):
        from backend.app.ingestion_service import enrich_saved_course
        self.content = visual_pdf(["Question 1"])
        self.run_ingestion();self.discover.reset_mock();provider = FakeVision()
        first = enrich_saved_course(42, store=self.store, vision_provider=provider)
        self.assertEqual(first["processed"], 1)
        self.assertEqual(self.store.load_document(42, "syllabus:42")["text"], "Late work policy")
        self.assertIn("x + y = 7", self.store.load_document(42, "file:9")["text"])
        self.assertEqual(len(provider.calls), 1)
        second = enrich_saved_course(42, store=self.store, vision_provider=provider)
        self.assertEqual(second["processed"], 0)
        self.assertEqual(len(provider.calls), 1)
        self.discover.assert_not_called()

    def test_locked_file_never_reaches_vision_and_does_not_resurrect_old_chunks(self):
        from backend.app.ingestion_service import enrich_saved_course
        self.content = visual_pdf(["Question 1"]);self.run_ingestion()
        self.metadata["locked_for_user"] = True;provider = FakeVision()
        enrich_saved_course(42, store=self.store, vision_provider=provider)
        self.assertEqual(provider.calls, [])
        self.assertEqual(self.store.load_chunks(42, "file:9"), [])


class OfficeVisualTests(unittest.TestCase):
    """The same provider/cache handles rendered slides and rendered Word pages."""

    setUp = EnrichmentTests.setUp

    def word(self):
        import docx
        document = docx.Document()
        document.add_paragraph("Read the question in the image.")
        document.add_picture(image_bytes())
        return save_bytes(document)

    def presentation(self):
        import pptx
        from pptx.util import Inches
        presentation = pptx.Presentation(io.BytesIO(make_pptx()))
        presentation.slides[0].shapes.add_picture(image_bytes(), Inches(4), Inches(2), width=Inches(2))
        return save_bytes(presentation)

    def test_word_keeps_original_blocks_and_uses_explicit_rendered_locations(self):
        with patch.object(legacy_office, "render_office_pdf", return_value=visual_pdf(["Read the question in the image."])):
            result = visual_document_service.extract_file({"filename": "Homework.docx"}, self.word(), store=self.store,
                                                         course_id=42, provider=self.provider)
        self.assertTrue(result["blocks"])
        self.assertEqual(result["blocks"][0]["block_number"], 1)
        self.assertEqual(result["rendered_pages"][0]["rendered_page_number"], 1)
        self.assertNotIn("page_number", result["rendered_pages"][0])
        self.store.save_document(42, {**result, "document_key": "file:8", "source_type": "file", "title": "Homework.docx"}, "now")
        ensure_course_chunks(42, self.store)
        chunk = self.store.load_chunks(42)[0]
        self.assertEqual(chunk["locations"][0]["kind"], "rendered_word_page")
        self.assertIn("x + y = 7", chunk["text"])

    def test_powerpoint_preserves_slide_number_and_speaker_notes(self):
        with patch.object(legacy_office, "render_office_pdf", return_value=visual_pdf(["Slide title"])):
            result = visual_document_service.extract_file({"filename": "Lecture.pptx"}, self.presentation(), store=self.store,
                                                         course_id=42, provider=self.provider)
        slide = result["slides"][0]
        self.assertEqual(slide["slide_number"], 1)
        self.assertNotIn("page_number", slide)
        self.assertEqual(slide["text"][slide["notes_start_character"]:], "Discuss time slices.")
        self.assertEqual(slide["extraction_method"], "native+vision")

    def test_disabled_powerpoint_vision_keeps_exact_native_text_and_notes(self):
        content = self.presentation();native = document_service.extract_pptx(content)
        with patch.object(legacy_office, "render_office_pdf", return_value=visual_pdf(["Different renderer formatting"])):
            result = visual_document_service.extract_file({"filename": "Lecture.pptx"}, content, store=self.store,
                                                         course_id=42, enabled=False)
        self.assertEqual(result["text"], native["text"])
        self.assertEqual(result["slides"][0]["notes_start_character"], native["slides"][0]["notes_start_character"])

    def test_disabled_word_vision_keeps_native_blocks_and_searchable_text(self):
        content = self.word();native = document_service.extract_docx(content)
        with patch.object(legacy_office, "render_office_pdf", return_value=visual_pdf(["Different renderer formatting"])):
            result = visual_document_service.extract_file({"filename": "Homework.docx"}, content, store=self.store,
                                                         course_id=42, enabled=False)
        self.assertEqual(result["text"], native["text"])
        self.assertEqual(result["blocks"], native["blocks"])
        self.assertNotIn("rendered_pages", result)
        self.assertTrue(result["visual_inspection_pages"])

    def test_slide_count_mismatch_never_invents_page_to_slide_mapping(self):
        with patch.object(legacy_office, "render_office_pdf", return_value=visual_pdf(["Slide A", "Slide B"])):
            result = visual_document_service.extract_file({"filename": "Lecture.pptx"}, self.presentation(), store=self.store,
                                                         course_id=42, provider=self.provider)
        self.assertEqual(result["status"], "partial")
        self.assertIn("visual_inspection_failed", result["warnings"])
        self.assertEqual(self.provider.calls, [])

    def test_missing_converter_preserves_native_content_and_clear_warning(self):
        with patch.object(legacy_office, "render_office_pdf", side_effect=legacy_office.LegacyConversionError("LibreOfficeNotInstalled")):
            result = visual_document_service.extract_file({"filename": "Homework.docx"}, self.word(), store=self.store,
                                                         course_id=42, provider=self.provider)
        self.assertEqual(result["status"], "partial")
        self.assertIn("Read the question", result["text"])
        self.assertIn("office_visual_rendering_unavailable", result["warnings"])

    def test_bad_office_render_cannot_replace_good_native_words_with_gibberish(self):
        with patch.object(legacy_office, "render_office_pdf", return_value=make_pdf(["Unreadable renderer garbage"] )):
            result = visual_document_service.extract_file({"filename": "Homework.docx"}, self.word(), store=self.store,
                                                         course_id=42, provider=self.provider)
        self.assertIn("Read the question in the image", result["text"])
        self.assertNotIn("rendered_pages", result)
        self.assertIn("rendered_native_text_mismatch", result["warnings"])

    def test_office_render_blocks_external_images_before_starting_converter(self):
        output = io.BytesIO()
        with ZipFile(io.BytesIO(self.word())) as source, ZipFile(output, "w") as target:
            for name in source.namelist():
                data = source.read(name)
                if name == "word/_rels/document.xml.rels":
                    data = data.replace(b"</Relationships>", b'<Relationship Id="external-image" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="https://external.example/image.png" TargetMode="External"/></Relationships>')
                target.writestr(name, data)
        with patch.object(legacy_office, "_run_conversion") as convert:
            with self.assertRaises(legacy_office.LegacyConversionError) as raised:
                legacy_office.render_office_pdf(output.getvalue(), "docx")
        self.assertEqual(raised.exception.error_type, "ExternalOfficeResourceBlocked")
        convert.assert_not_called()

    @unittest.skipUnless(os.getenv("EASYCANVAS_TEST_LEGACY_CONVERSION") == "1", "Requires real LibreOffice")
    def test_real_word_and_powerpoint_rendering_with_fake_vision(self):
        for format_name, data in (("docx", self.word()), ("pptx", self.presentation())):
            result = visual_document_service.extract_file({"filename": f"Material.{format_name}"}, data,
                                                         store=self.store, course_id=42, provider=self.provider)
            self.assertIn("visual_processing", result)
            self.assertNotIn("error_type", result["visual_processing"])
            self.assertIn("x + y = 7", result["text"])


class DocumentResolutionTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory();self.addCleanup(self.directory.cleanup)
        self.store = IngestionStore("student", Path(self.directory.name) / "d.sqlite3")
        self.store.save_course({"course_id": 42, "synced_at": "now"})
        for fid, title, text in ((1, "Practice Questions 1.pdf", "Question 1: process scheduling."),
                                 (10, "Practice Questions 10.pdf", "Question 1: matrix orthogonality.")):
            self.store.save_document(42, {"document_key": f"file:{fid}", "canvas_id": fid, "course_id": 42,
                                         "source_type": "file", "title": title, "status": "ok", "text": text}, "now")
        ensure_course_chunks(42, self.store)
        self.provider = FakeProvider();embed_course(42, store=self.store, provider=self.provider)

    def test_numbers_extensions_case_and_separators_are_preserved(self):
        documents = self.store.load_documents(42)
        for question in ("What is Question 1 in Practice Questions 10?", "List PRACTICE_QUESTIONS_10.PDF", "practice-questions-10"):
            self.assertEqual(resolve_documents(question, documents), ["file:10"])
        self.assertEqual(resolve_documents("Practice Questions 1", documents), ["file:1"])
        self.assertEqual(resolve_documents("PracticeQuestions10.pdf", documents), ["file:10"])

    def test_explicit_document_filters_all_sources_before_semantic_ranking(self):
        result = retrieve_evidence(42, "Question 1 in Practice Questions 10", store=self.store, provider=self.provider)
        self.assertTrue(result["results"])
        self.assertEqual({o["document_key"] for r in result["results"] for o in r["sources"]}, {"file:10"})

    def test_exact_filename_beats_numbered_copy_with_the_same_prefix(self):
        documents = [{"source_type": "file", "document_key": "file:a", "title": "Joining Class Fall 2026.pptx"},
                     {"source_type": "file", "document_key": "file:b", "title": "Joining Class Fall 2026-1.pptx"}]
        self.assertEqual(resolve_documents("Explain Joining Class Fall 2026.pptx", documents), ["file:a"])

    def test_explicit_extension_distinguishes_same_stem_pdf_and_text(self):
        documents = [{"source_type": "file", "document_key": "file:a", "title": "Static Analysis.pdf"},
                     {"source_type": "file", "document_key": "file:b", "title": "Static Analysis.txt"}]
        self.assertEqual(resolve_documents("Explain Static Analysis.pdf", documents), ["file:a"])

    def test_rare_document_terms_can_recover_evidence_outside_semantic_pool(self):
        def item(n, text):
            return {"text_hash": str(n), "text": text, "sources": [{"document_key": "file:10", "course_id": 42,
                     "chunk_index": n, "title": "Practice Questions 10.pdf", "source_type": "file", "locations": [],
                     "source_quality": {"policy_retrieval_eligible": True}}]}
        ordinary = item(0, "A general overview of the homework.")
        specific = item(40, "The screenshot identifies a buffer overflow in the parser.")
        store = Mock();store.load_documents.return_value = self.store.load_documents(42)
        store.load_embeddings.return_value = {"0": [1], "40": [1]}
        store.load_embedding_inputs.return_value = [ordinary, specific]
        with patch("backend.app.retrieval_service.search_course", return_value={"status": "ready", "results": [{**ordinary, "score": .9}]}):
            result = retrieve_evidence(42, "In Practice Questions 10.pdf, what does the screenshot say about buffer overflow?", store=store)
        self.assertTrue(any("buffer overflow" in item["text"] for item in result["results"]))

    def test_general_question_keeps_course_wide_search(self):
        result = search_course(42, "Explain matrix", store=self.store, provider=self.provider)
        self.assertEqual(len(result["results"]), 2)

    def test_course_code_alone_does_not_resolve_an_assignment_filename(self):
        documents = [{"source_type": "file", "document_key": "file:a", "title": "CSE 1234 Assignment 1.pdf"}]
        self.assertEqual(resolve_documents("In CSE 1234, explain orthogonality", documents), [])
        self.assertEqual(resolve_documents("Explain CSE 1234 Assignment 1", documents), ["file:a"])

    def test_single_word_filename_resolves_only_when_explicit(self):
        documents = [{"source_type": "file", "document_key": "file:a", "title": "Lecture.pdf"}]
        self.assertEqual(resolve_documents("What does Lecture.pdf say?", documents), ["file:a"])
        self.assertEqual(resolve_documents("Explain this lecture", documents), [])

    def test_follow_up_focus_stays_in_selected_document(self):
        result = retrieve_evidence(42, "Explain its first question", focus_documents=["file:10"], store=self.store, provider=self.provider)
        self.assertEqual({o["document_key"] for r in result["results"] for o in r["sources"]}, {"file:10"})

    def test_named_unindexed_document_does_not_fall_back_to_another_file(self):
        self.store.save_document(42, {"document_key": "file:11", "source_type": "file", "title": "Practice Questions 11.pdf", "status": "ok", "text": "New unindexed question"}, "now")
        ensure_course_chunks(42, self.store)
        result = retrieve_evidence(42, "Practice Questions 11", store=self.store, provider=self.provider)
        self.assertEqual(result["status"], "not_indexed")
        self.assertEqual(result["results"], [])

    def test_list_request_includes_document_order_and_coverage(self):
        result = retrieve_evidence(42, "List all questions in Practice Questions 10", store=self.store, provider=self.provider)
        self.assertEqual(result["evidence_coverage"], {"selected": 1, "available": 1})
        self.assertEqual(result["results"][0]["selection"], "document_order")
