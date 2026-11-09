"""Only pending visual work is reprocessed; complete pages retain their cache."""

import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.app.ingestion_service import _cached_text, enrich_saved_course
from backend.app.ingestion_store import IngestionStore
from backend.app.visual_document_service import pending_visual_content, extract_file
from backend.tests.test_visual_documents import visual_pdf, FakeVision


class PendingVisualTests(unittest.TestCase):
    def test_partial_alone_is_not_a_visual_retry(self):
        self.assertFalse(pending_visual_content({'format':'doc','status':'partial','warnings':['legacy_doc_structure_unverified']}))

    def test_pending_and_failed_units_are_selected(self):
        for status in ('vision_pending','vision_failed','rendered_content_missing'):
            self.assertTrue(pending_visual_content({'format':'pdf','pages':[{'extraction_status':status}]}))

    def test_completed_genuine_unreadable_region_is_not_repeatedly_paid_for(self):
        self.assertFalse(pending_visual_content({'format':'pdf','status':'partial','warnings':[],
            'pages':[{'extraction_status':'partial','extraction_method':'vision','unreadable':['A blurred number.']}],
            'visual_processing':{'pending':0,'failed':0,'enabled':True}}))

    def test_enabling_vision_reuses_complete_compatible_extraction(self):
        previous = {'source_type':'file','format':'pdf','status':'ok','text':'Already readable','stale':False,
                    'latest_attempt':{'version':'one','pipeline_version':'native:inspection-only:config','status':'ok'}}
        document = {'version':'one','pipeline_version':'native:enabled:config'}
        self.assertIsNotNone(_cached_text(previous,document,False))
        previous['warnings']=['images_not_extracted']
        self.assertIsNone(_cached_text(previous,document,False))

    def test_real_parser_change_still_invalidates_cache(self):
        previous = {'source_type':'file','format':'pdf','status':'ok','text':'Readable','stale':False,
                    'latest_attempt':{'version':'one','pipeline_version':'native-v1:inspection-only:config','status':'ok'}}
        self.assertIsNone(_cached_text(previous,{'version':'one','pipeline_version':'native-v2:enabled:config'},False))

    def test_embedding_refresh_can_be_limited_to_changed_documents(self):
        from backend.evaluation.fixtures import EvaluationFixture
        from backend.app.embedding_service import embed_course
        fixture = EvaluationFixture();self.addCleanup(fixture.close)
        fixture.save('file:901','Changed.txt','New readable material about matrices.')
        fixture.save('file:902','Unrelated.txt','Another new input that must not be indexed by this run.')
        from backend.app.chunking_service import ensure_course_chunks
        ensure_course_chunks(42,fixture.store)
        result = embed_course(42,store=fixture.store,provider=fixture.embedding,document_keys=['file:901'])
        self.assertEqual(result['generated'],1)
        texts = [t for batch in fixture.embedding.calls for t in batch]
        self.assertFalse(any('Another new input' in t for t in texts))


class VisualBackfillTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory();self.addCleanup(directory.cleanup)
        self.store = IngestionStore('test',Path(directory.name)/'data.sqlite3')
        self.store.save_course({'course_id':42,'synced_at':'fixed'})

    def test_completed_page_is_reused_when_only_neighbor_is_pending(self):
        content = visual_pdf(['First question','Second question'])
        first = extract_file({'filename':'questions.pdf'},content,store=self.store,course_id=42,
                             provider=FakeVision(fail_on=2))
        self.assertEqual(first['visual_processing']['generated'],1)
        inspected = extract_file({'filename':'questions.pdf'},content,store=self.store,course_id=42,
                                 provider=FakeVision(),enabled=False)
        self.assertEqual(inspected['visual_processing']['reused'],1)
        self.assertEqual(inspected['visual_processing']['pending'],1)
        provider = FakeVision()
        result = extract_file({'filename':'questions.pdf'},content,store=self.store,course_id=42,provider=provider)
        self.assertEqual(len(provider.calls),1)
        self.assertEqual(result['visual_processing']['reused'],1)
        self.assertEqual(result['status'],'ok')

    def test_complete_files_are_not_downloaded_or_reparsed(self):
        self.store.save_document(42,{'document_key':'file:1','source_type':'file','format':'pdf','canvas_id':1,
            'status':'ok','text':'Complete readable material','pipeline_version':'old:inspection-only:config'},'fixed')
        with patch('backend.app.canvas_service.get_file_info') as info:
            result = enrich_saved_course(42,store=self.store,vision_provider=FakeVision())
        self.assertEqual(result['processed'],0)
        info.assert_not_called()

    def test_saved_external_document_uses_existing_safe_downloader(self):
        content = visual_pdf(['Question'])
        native = extract_file({'filename':'syllabus.pdf'},content,store=self.store,course_id=42,enabled=False)
        original = {**native,'document_key':'syllabus_file:test','canvas_id':None,'source_type':'syllabus_file',
                    'title':'syllabus.pdf','url':'https://syllabus.example/document.pdf','source_role':'course_syllabus',
                    'pipeline_version':'native-pipeline','references':[{'source_type':'syllabus','canvas_id':42}]}
        self.store.save_document(42,original,'fixed')
        with patch('backend.app.syllabus_service.fetch_syllabus',return_value=(content,{'etag':'one'})) as fetch, \
             patch('backend.app.canvas_service.get_file_info') as info:
            result = enrich_saved_course(42,store=self.store,vision_provider=FakeVision())
            second = enrich_saved_course(42,store=self.store,vision_provider=FakeVision())
        self.assertEqual(result['processed'],1)
        self.assertEqual(second['processed'],0)
        fetch.assert_called_once_with(original['url'],42)
        info.assert_not_called()
        saved = self.store.load_document(42,original['document_key'])
        self.assertEqual(saved['status'],'ok')
        self.assertEqual(saved['pipeline_version'],'native-pipeline')
        self.assertEqual(saved['source_role'],'course_syllabus')
        self.assertEqual(saved['version'],saved['content_hash'])
        self.assertEqual(saved['references'],original['references'])
        self.assertEqual(saved['pages'][0]['extraction_method'],'vision')
