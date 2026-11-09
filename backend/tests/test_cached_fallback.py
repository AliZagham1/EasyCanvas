"""Refresh failures must not hide unchanged, successfully processed course text."""

import tempfile
import unittest
from pathlib import Path

import requests

from backend.app.chunking_service import ensure_course_chunks
from backend.app.ingestion_store import IngestionStore
from backend.app.embedding_service import embed_course
from backend.app.retrieval_service import search_course
from backend.tests.test_embeddings import FakeProvider
from backend.tests import test_syllabus_service, test_ingestion_pipeline


class CachedSyllabusTests(unittest.TestCase):
    setUp = test_syllabus_service.SyllabusTests.setUp
    ingest = test_syllabus_service.SyllabusTests.ingest

    def test_cached_syllabus_connection_failure_keeps_chunks_and_embeddings_searchable(self):
        old = self.ingest()[0][0]
        provider = FakeProvider()
        embed_course(42, store=self.store, provider=provider)
        before = self.store.load_chunks(42)
        self.fetch.side_effect = requests.ConnectionError('DNS failed')
        self.ingest()
        current = self.store.load_document(42, old['document_key'])
        self.assertEqual(len(self.store.load_chunks(42)),len(before))
        self.assertTrue(current['content_usable'])
        self.assertEqual(current['cache_status'], 'stale_but_usable')
        self.assertEqual(current['refresh_status'], 'error')
        self.assertEqual(current['latest_attempt']['error_type'], 'ConnectionError')
        self.assertEqual([c['text'] for c in self.store.load_chunks(42)], [c['text'] for c in before])
        self.assertEqual(ensure_course_chunks(42, self.store)['processed'], 0)
        self.assertEqual(embed_course(42, store=self.store, provider=provider)['generated'], 0)
        result = search_course(42, 'What is the attendance policy?', store=self.store, provider=provider)
        self.assertEqual(result['status'], 'ready')
        self.assertTrue(any('Attendance policy' in item['text'] for item in result['results']))

    def test_failed_refresh_does_not_clear_error_during_quality_reassessment(self):
        old = self.ingest()[0][0]
        self.fetch.side_effect = requests.ConnectionError()
        self.ingest()
        self.store.reassess_document_quality(42)
        self.assertEqual(self.store.load_document(42, old['document_key'])['refresh_status'], 'error')

    def test_later_success_reuses_cached_extraction_and_clears_error(self):
        old = self.ingest()[0][0]
        self.fetch.side_effect = requests.ConnectionError()
        self.ingest()
        self.fetch.side_effect = None
        self.fetch.return_value = (None, {'etag':'"v1"'})
        result = self.ingest()[0]
        self.assertEqual(result[1], 'reused')
        self.assertEqual(self.fetch.call_args.args[2]['etag'], '"v1"')
        current = self.store.load_document(42, old['document_key'])
        self.assertEqual(current['cache_status'], 'current')
        self.assertEqual(current['refresh_status'], 'ok')
        self.assertIsNone(current['last_refresh_error'])
        self.assertEqual(current['text'], old['text'])


class CachedFilePipelineTests(unittest.TestCase):
    setUp = test_ingestion_pipeline.IngestionPipelineTests.setUp
    run_ingestion = test_ingestion_pipeline.IngestionPipelineTests.run_ingestion

    def test_cached_pdf_download_timeout_remains_searchable(self):
        self.run_ingestion()
        provider = FakeProvider()
        embed_course(42,store=self.store,provider=provider)
        original = self.store.load_document(42,'file:9')
        self.download.side_effect = requests.Timeout()
        self.run_ingestion(force=True)
        current = self.store.load_document(42,'file:9')
        self.assertEqual(current['cache_status'],'stale_but_usable')
        self.assertEqual(current['text'],original['text'])
        self.assertEqual(current['references'],original['references'])
        result = search_course(42,'Explain Lecture.pdf',store=self.store,provider=provider)
        self.assertEqual(result['status'],'ready')
        self.assertEqual(result['results'][0]['sources'][0]['document_key'],'file:9')
        self.assertEqual(result['results'][0]['sources'][0]['locations'][0]['page_number'],1)

    def test_no_cached_file_and_connection_failure_has_no_searchable_content(self):
        self.download.side_effect = requests.ConnectionError()
        self.run_ingestion()
        current = self.store.load_document(42,'file:9')
        self.assertFalse(current['content_usable'])
        self.assertEqual(self.store.load_chunks(42,'file:9'),[])

    def test_later_successful_unchanged_metadata_check_needs_no_redownload(self):
        self.run_ingestion()
        self.download.side_effect = requests.Timeout()
        self.run_ingestion(force=True)
        report = self.run_ingestion()
        self.assertEqual(self.download.call_count,2)
        self.assertEqual(report['statistics']['downloads'],0)
        self.assertEqual(self.store.load_document(42,'file:9')['cache_status'],'current')


class CacheStateTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory();self.addCleanup(directory.cleanup)
        self.store = IngestionStore('student',Path(directory.name)/'test.sqlite3')
        self.store.save_course({'course_id':42,'synced_at':'fixed'})
        self.document = {'document_key':'file:9','canvas_id':9,'course_id':42,'source_type':'file','title':'Policy.pdf',
            'status':'ok','version':'v1','text':'Attendance below 80 percent reduces the overall score by five points.',
            'references':[{'source_type':'module_item','module_id':7,'module_name':'Course information'}]}
        self.store.save_document(42,self.document,'fixed')
        ensure_course_chunks(42,self.store)
        self.provider = FakeProvider();embed_course(42,store=self.store,provider=self.provider)
        self.last_success = self.store.load_document(42,'file:9')['last_successful_refresh']

    def fail(self, **fields):
        attempt = {**self.document,'status':'error','text':'','error_type':'ConnectionError',**fields}
        self.store.save_document(42,attempt,'fixed')
        return self.store.load_document(42,'file:9')

    def test_retryable_http_and_network_failures_preserve_cache(self):
        for fields in ([{'http_status':code,'error_type':'HTTPError'} for code in (408,429,500,502,503,504)]
                       + [{'error_type':kind} for kind in ('ConnectionError','Timeout','ConnectTimeout','ReadTimeout','ProxyError','ChunkedEncodingError')]):
            with self.subTest(fields=fields):
                current = self.fail(**fields)
                self.assertTrue(current['content_usable'])
                self.assertIsNone(current['cache_blocked'])
                self.assertEqual(current['last_successful_refresh'],self.last_success)
                self.assertEqual(len(self.store.load_chunks(42)),1)

    def test_access_denial_deletion_and_locked_state_block_cache(self):
        for fields in ([{'http_status':code} for code in (401,403,404,410)] + [{'error_type':'LockedForUser','status':'unavailable'}]):
            with self.subTest(fields=fields):
                self.store.save_document(42,self.document,'fixed')
                current = self.fail(**fields)
                self.assertFalse(current['content_usable'])
                self.assertEqual(self.store.load_chunks(42),[])
                self.assertTrue(self.store.load_embeddings(42,self.provider.configuration))

    def test_later_network_error_does_not_resurrect_revoked_content(self):
        self.fail(http_status=403,status='unavailable',error_type='HTTPError')
        current = self.fail()
        self.assertEqual(current['cache_blocked'],'access_revoked')
        self.assertFalse(current['content_usable'])
        self.assertEqual(self.store.load_embedding_inputs(42),[])

    def test_confirmed_remote_change_does_not_silently_serve_old_version(self):
        current = self.fail(version='v2',error_type='Timeout')
        self.assertEqual(current['cache_blocked'],'changed_content')
        self.assertFalse(current['content_usable'])
        self.assertFalse(self.fail(version=None)['content_usable'])

    def test_unknown_parser_tls_and_invalid_content_failures_are_not_network_fallback(self):
        for kind in ('ValueError','PdfReadError','SSLError','MissingBody'):
            with self.subTest(kind=kind):
                self.store.save_document(42,self.document,'fixed')
                self.assertFalse(self.fail(error_type=kind)['content_usable'])

    def test_successful_changed_content_replaces_chunks_and_reuses_only_matching_vectors(self):
        old_success = self.store.load_document(42,'file:9')['last_successful_refresh']
        self.fail()
        changed = {**self.document,'text':'The new policy requires attendance at every meeting.','version':'v2'}
        self.store.save_document(42,changed,'fixed')
        current = self.store.load_document(42,'file:9')
        self.assertEqual(current['cache_status'],'current')
        self.assertIsNone(current['last_refresh_error'])
        self.assertIsNone(current['cache_blocked'])
        self.assertNotEqual(current['last_successful_refresh'],old_success)
        self.assertEqual(self.store.load_chunks(42),[])  # Old fingerprint cannot escape.
        self.assertEqual(ensure_course_chunks(42,self.store)['processed'],1)
        self.assertEqual(embed_course(42,store=self.store,provider=self.provider)['generated'],1)
        self.assertEqual(self.store.load_chunks(42)[0]['text'],changed['text'])

    def test_blank_or_damaged_cached_text_is_not_promoted(self):
        for payload in ({**self.document,'text':'   '}, {**self.document,'blocks':[{'text':17}]}):
            with self.subTest(payload=payload):
                import json
                with self.store._connection() as c:
                    c.execute('UPDATE documents SET document_json=? WHERE namespace=? AND course_id=? AND document_key=?',
                              (json.dumps(payload),self.store.namespace,42,'file:9'))
                current = self.fail()
                self.assertFalse(current['content_usable'])

    def test_removed_sources_and_other_accounts_remain_excluded(self):
        self.fail()
        other = IngestionStore('other',self.store.path)
        other.save_course({'course_id':42,'synced_at':'fixed'})
        self.assertEqual(other.load_chunks(42),[])
        self.store.save_course({'course_id':43,'synced_at':'fixed'})
        self.assertEqual(self.store.load_chunks(43),[])
        self.store.save_course({'course_id':42,'synced_at':'later'})
        self.assertEqual(self.store.load_chunks(42),[])
