"""Real synthetic PDF objects with declared visual transcriptions; no paid OCR."""

import io
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw
from pypdf import PdfReader, PdfWriter
from pypdf.generic import DictionaryObject, NameObject, NumberObject, DecodedStreamObject

from backend.app.chunking_service import ensure_course_chunks
from backend.app.embedding_service import embed_course
from backend.app.ingestion_store import IngestionStore
from backend.app.retrieval_service import search_course
from backend.app.visual_document_service import extract_file
from backend.app.vision_service import VisionConfiguration
from backend.tests.test_document_service import make_pdf
from backend.tests.test_embeddings import FakeProvider

VISUAL_CASES = (
    ('selectable_text', 'Normal course explanation with fully selectable text.', None),
    ('scanned_text', None, 'Exercise: explain the process lifecycle and ready queue.'),
    ('mixed_screenshot', 'A selectable introduction to an important screenshot. ' * 20, 'Screenshot: the process changes from ready to running.'),
    ('matrix_equation', 'Matrix example', 'Matrix A = [[1, 0], [0, 1]]. Equation: Ax = b.'),
    ('code_screenshot', 'Code example', 'int countPositive(int[] x) { return 0; }'),
    ('table', 'Table example', 'State | Meaning\nReady | Waiting for CPU\nRunning | Using CPU'),
    ('diagram', 'Diagram example', 'Node A -> Node B -> Node C'),
)


def pdf_content(native_text, visual_text):
    """Place actual text/image pixels in memory, keeping fixture PDFs uncommitted."""
    reader = PdfReader(io.BytesIO(make_pdf([native_text])))
    writer = PdfWriter()
    page = writer.add_page(reader.pages[0])
    original = page.get_contents()
    native_data = original.get_data() if original is not None else b''
    if visual_text:
        pixels = Image.new('RGB', (650, 180), 'white')
        ImageDraw.Draw(pixels).multiline_text((10, 10), visual_text, fill='black')
        image = DecodedStreamObject()
        image.set_data(pixels.tobytes())
        image.update({NameObject('/Type'):NameObject('/XObject'), NameObject('/Subtype'):NameObject('/Image'),
                      NameObject('/Width'):NumberObject(650), NameObject('/Height'):NumberObject(180),
                      NameObject('/ColorSpace'):NameObject('/DeviceRGB'), NameObject('/BitsPerComponent'):NumberObject(8)})
        resources = page.get('/Resources') or DictionaryObject()
        resources[NameObject('/XObject')] = DictionaryObject({NameObject('/Visual'):writer._add_object(image)})
        page[NameObject('/Resources')] = resources
        stream = DecodedStreamObject()
        stream.set_data(native_data + b'\nq 240 0 0 100 20 20 cm /Visual Do Q')
        page[NameObject('/Contents')] = writer._add_object(stream)
    result = io.BytesIO()
    writer.write(result)
    return result.getvalue()


class DeclaredVision:
    def __init__(self, text):
        self.configuration = VisionConfiguration(model='declared-fixture-not-real-vision')
        self.text, self.calls = text, 0

    def extract_page(self, image_bytes, *, native_text):
        self.calls += 1
        return {'extraction':{'complete':True, 'unreadable':[],
                 'elements':[{'type':'text','text':text} for text in (native_text, self.text) if text]}, 'usage':None}


def evaluate_visual_case(name, native_text, visual_text):
    """Verify detection → transcription handoff → SQLite → chunks → search metadata."""
    with tempfile.TemporaryDirectory() as directory:
        store = IngestionStore('evaluation', Path(directory) / 'visual.sqlite3')
        store.save_course({'course_id':42,'synced_at':'fixed'})
        provider = DeclaredVision(visual_text)
        extracted = extract_file({'filename':name+'.pdf'}, pdf_content(native_text, visual_text),
                                 store=store, course_id=42, provider=provider, enabled=True)
        store.save_document(42, {**extracted,'document_key':'file:900','canvas_id':900,'course_id':42,
            'source_type':'file','title':name+'.pdf','url':'https://canvas.example/courses/42/files/900'}, 'fixed')
        ensure_course_chunks(42, store)
        embedding = FakeProvider()
        embed_course(42, store=store, provider=embedding)
        search = search_course(42, visual_text or native_text, store=store, provider=embedding)
        failures = []
        if provider.calls != int(visual_text is not None):
            failures.append('Vision was not invoked only on the expected visual page.')
        if (visual_text or native_text) not in extracted['text']:
            failures.append('Expected declared content is missing after extraction.')
        if not search['results']:
            failures.append('Extracted content did not become searchable.')
        origins = [o for item in search['results'] for o in item['sources']]
        if any(o['document_key']!='file:900' or not any(loc.get('page_number')==1 for loc in o['locations']) for o in origins):
            failures.append('Page/document provenance was lost.')
        return {'vision_calls':provider.calls,'extraction_status':extracted['status'],
                'extracted_text':extracted['text'],'search_results':search['results']}, failures
