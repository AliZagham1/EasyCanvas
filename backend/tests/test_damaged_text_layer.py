"""EC-025: a Word render with substitute fonts can damage the PDF text layer; detect it, re-render with safe fonts."""

import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from zipfile import ZipFile

import docx

from backend.app import legacy_office, visual_document_service as vds
from backend.app.ingestion_store import IngestionStore
from backend.tests.test_office_documents import image_bytes, save_bytes
from backend.tests.test_visual_documents import FakeVision, visual_pdf

SENTENCES = [
    "The instructor will return graded homework within one week after every submission deadline",
    "Students must attend laboratory sessions and complete programming assignments individually",
    "Late work loses ten percent each day unless the professor approves an extension request",
    "Midterm examinations cover recursion pointers memory allocation sorting algorithms and graphs",
    "Office hours are held every Tuesday afternoon in the engineering building second floor",
    "Academic integrity violations are reported to the department and may fail the course",
]
CLEAN = ". ".join(SENTENCES) + "."


def garble(text):
    out = text.replace("c", ",")
    for word in ("graded", "homework", "students", "professor", "algorithms", "afternoon", "building", "reported", "violations"):
        out = out.replace(word, word[:2] + word[1] + word[2:])
    return out


def word_bytes():
    document = docx.Document()
    for sentence in SENTENCES:
        document.add_paragraph(sentence + ".")
    document.add_picture(image_bytes())   # a picture is what sends a Word file through the visual path
    return save_bytes(document)


class DetectorTests(unittest.TestCase):
    def test_clean_render_is_not_flagged_but_damaged_one_is(self):
        self.assertFalse(vds.text_layer_garbled(CLEAN, CLEAN))
        self.assertTrue(vds.text_layer_garbled(CLEAN, garble(CLEAN)))

    def test_short_reference_is_never_judged(self):
        self.assertFalse(vds.text_layer_garbled("short text", "sh,rt text"))

    def test_math_commas_in_the_source_are_not_damage(self):
        reference = CLEAN + " Use f(a,b) and g(x,y) here."
        self.assertFalse(vds.text_layer_garbled(reference, reference))


class NormalizerTests(unittest.TestCase):
    def test_fonts_are_replaced_but_code_fonts_stay(self):
        document = docx.Document()
        run = document.add_paragraph().add_run("hello"); run.font.name = "SimSun"
        code = document.add_paragraph().add_run("x = 1"); code.font.name = "Courier New"
        fixed = legacy_office.normalize_docx_fonts(save_bytes(document))
        xml = ZipFile(io.BytesIO(fixed)).read("word/document.xml").decode()
        self.assertNotIn("SimSun", xml)
        self.assertIn("Courier New", xml)
        self.assertIn("Arial", xml)

    def test_non_docx_bytes_are_an_explicit_error(self):
        with self.assertRaises(legacy_office.LegacyConversionError):
            legacy_office.normalize_docx_fonts(b"not a zip")


class BranchTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(); self.addCleanup(directory.cleanup)
        self.store = IngestionStore("student", Path(directory.name) / "i.sqlite3")
        self.store.save_course({"course_id": 42, "synced_at": "now"})

    def run_extract(self, renders):
        with patch.object(legacy_office, "render_office_pdf", side_effect=renders) as render:
            result = vds.extract_file({"filename": "Syllabus.docx"}, word_bytes(), store=self.store, course_id=42,
                                      provider=FakeVision(), enabled=True)
        return result, render

    def test_damaged_render_is_re_rendered_and_the_clean_one_is_used(self):
        result, render = self.run_extract([visual_pdf([garble(CLEAN)], image=False), visual_pdf([CLEAN], image=False)])
        self.assertEqual(render.call_count, 2)
        self.assertEqual(result["visual_processing"]["text_layer"], "repaired")
        self.assertIn("instructor will return", result["text"])
        self.assertNotIn("rendered_text_layer_unreliable", result.get("warnings", []))

    def test_unrepairable_render_is_marked_unreliable(self):
        bad = visual_pdf([garble(CLEAN)], image=False)
        result, _ = self.run_extract([bad, bad])
        self.assertEqual(result["visual_processing"]["text_layer"], "unreliable")
        self.assertIn("rendered_text_layer_unreliable", result["warnings"])

    def test_clean_render_is_rendered_once(self):
        result, render = self.run_extract([visual_pdf([CLEAN], image=False)])
        self.assertEqual(render.call_count, 1)
        self.assertEqual(result["visual_processing"]["text_layer"], "ok")


class PendingTests(unittest.TestCase):
    def document(self, marker=None):
        report = {"text_layer": marker} if marker else {}
        return {"format": "docx", "blocks": [{"text": CLEAN}], "visual_processing": report,
                "rendered_pages": [{"extraction_method": "native", "text": garble(CLEAN)}]}

    def test_old_damaged_document_is_queued_once(self):
        self.assertTrue(vds.pending_visual_content(self.document()))
        self.assertFalse(vds.pending_visual_content(self.document("unreliable")))


if __name__ == "__main__":
    unittest.main()


import backend.tests.ai_consent_granted  # noqa: E402,F401  (AI tests use fake providers)
