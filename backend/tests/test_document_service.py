import io
import json
import unittest

from pypdf import PdfWriter
from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject

from backend.app.document_service import extract_file, extract_html, extract_pdf, extract_notebook


def make_pdf(texts, password=None):
    """Create real tiny PDFs so tests exercise the parser instead of mocking it."""
    writer = PdfWriter()
    for text in texts:
        page = writer.add_blank_page(width=300, height=300)
        if text:
            font = DictionaryObject({NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"), NameObject("/BaseFont"): NameObject("/Helvetica")})
            page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})})
            stream = DecodedStreamObject()
            stream.set_data(f"BT /F1 12 Tf 20 250 Td ({text}) Tj ET".encode("ascii"))
            page[NameObject("/Contents")] = writer._add_object(stream)
    if password:
        writer.encrypt(password)
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


class DocumentServiceTests(unittest.TestCase):
    def test_pdf_text_and_page_numbers_are_preserved(self):
        result = extract_pdf(make_pdf(["First lecture", "Second lecture"]))
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["page_count"], 2)
        self.assertEqual(result["pages"][1]["page_number"], 2)
        self.assertIn("First lecture", result["text"])
        self.assertIn("Second lecture", result["text"])
        self.assertEqual(result["character_count"], len(result["text"]))
        self.assertEqual(len(result["content_hash"]), 64)

    def test_blank_and_partially_readable_pdfs_are_explicit(self):
        empty = extract_pdf(make_pdf([None]))
        self.assertEqual(empty["status"], "no_text")
        self.assertEqual(empty["text"], "")
        partial = extract_pdf(make_pdf(["Readable", None]))
        self.assertEqual(partial["status"], "partial")
        self.assertEqual(partial["pages"][1]["status"], "no_text")

    def test_encrypted_and_invalid_files_do_not_crash(self):
        self.assertEqual(extract_pdf(make_pdf(["Secret"], "password"))["status"], "encrypted")
        self.assertEqual(extract_pdf(b"<html>Login page</html>")["error_type"], "InvalidPDF")
        with self.assertLogs("pypdf", level="WARNING"):
            self.assertEqual(extract_pdf(b"%PDF-invalid")["status"], "error")

    def test_html_keeps_words_and_discards_scripts(self):
        result = extract_html('<h1>Syllabus</h1><p>Read &amp; practice.</p><script>hidden()</script><style>hidden</style><p>Next section</p>')
        self.assertEqual(result["status"], "ok")
        self.assertIn("Read & practice.", result["text"])
        self.assertNotIn("hidden", result["text"])
        self.assertIn("\n", result["text"])

    def test_file_dispatch_handles_text_and_unsupported_types(self):
        self.assertEqual(extract_file({"filename": "lecture.pdf"}, make_pdf(["Lecture"]))["status"], "ok")
        self.assertEqual(extract_file({"content-type": "text/plain"}, b"Hello\r\nWorld")["text"], "Hello\nWorld")
        self.assertEqual(extract_file({"filename": "notes.html"}, b"<p>Notes</p>")["text"], "Notes")
        self.assertEqual(extract_file({"filename": "sheet.xlsx"}, b"binary")["status"], "unsupported")
        self.assertEqual(extract_file({"filename": "bad.txt"}, b"\xffinvalid")["status"], "error")

    def test_notebook_reads_instructions_and_code_without_running_it(self):
        content = json.dumps({"nbformat": 4, "cells": [
            {"cell_type": "markdown", "source": ["# Assignment\n", "Find the eigenvalues."]},
            {"cell_type": "code", "source": "raise RuntimeError('Do not execute this')", "outputs": []},
        ]}).encode()
        result = extract_file({"filename": "assignment.ipynb"}, content)
        self.assertEqual(result["status"], "ok")
        self.assertIn("Find the eigenvalues.", result["text"])
        self.assertIn("raise RuntimeError", result["text"])
        self.assertEqual(result["cells"][1]["cell_number"], 2)
        cell = result["cells"][1]
        self.assertIn("raise RuntimeError", result["text"][cell["start_character"]:cell["end_character"]])
        self.assertIsNone(result["page_count"])

    def test_notebook_outputs_images_and_bad_cells_are_marked_incomplete(self):
        content = json.dumps({"nbformat": 4, "cells": [
            {"cell_type": "markdown", "source": "Read this figure: ![Figure](attachment:figure.png)"},
            {"cell_type": "code", "source": "print(1)", "outputs": [{"text": "1"}]},
            {"cell_type": "unknown", "source": "unsupported"},
        ]}).encode()
        result = extract_notebook(content)
        self.assertEqual(result["status"], "partial")
        self.assertIn("Read this figure", result["text"])

    def test_invalid_and_empty_notebooks_have_explicit_results(self):
        for content in (b"invalid", b"[]", b"\xff", b'{"nbformat":3,"cells":[]}'):
            self.assertEqual(extract_notebook(content)["status"], "error")
        self.assertEqual(extract_notebook(b'{"nbformat":4,"cells":[]}')["status"], "no_text")

    def test_html_preserves_existing_image_descriptions_without_fetching_images(self):
        result = extract_html('<p>Use <img src="https://example.com/math.png" alt="x squared"> here.</p>')
        self.assertIn("x squared", result["text"])


if __name__ == "__main__":
    unittest.main()
