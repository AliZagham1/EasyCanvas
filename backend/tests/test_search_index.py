"""Automatic indexing uses temporary SQLite and fake providers, never API credit."""
import asyncio
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from backend.app import ingestion_service, main, course_sync_service
from backend.app.chunking_service import ensure_course_chunks
from backend.app.embedding_service import EmbeddingConfiguration, EmbeddingProviderError
from backend.app.ingestion_store import IngestionStore
from backend.app.retrieval_service import search_course
from backend.app.search_index_service import index_status, queue_course, recover_jobs, process_jobs
from backend.tests.test_ingestion_pipeline import snapshot
from backend.tests.test_retention_api import request


class Provider:
    def __init__(self):
        self.configuration = EmbeddingConfiguration()
        self.calls = []
        self.fail_on = None
    def embed_texts(self, texts):
        self.calls.append(texts)
        if len(self.calls) == self.fail_on:
            raise EmbeddingProviderError('private details must not escape')
        return {'vectors': [[1.] + [0.] * 1535 for _ in texts], 'prompt_tokens': len(texts)}


class IndexTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.store = IngestionStore('student', Path(self.directory.name) / 'test.sqlite3')
        self.store.save_course({'course_id': 42, 'synced_at': 'first'})
        self.store.save_sync_state({'courses': [{'course_id': 42}], 'status': 'completed'})
        self.provider = Provider()
    def save(self, key='file:1', text='Processes are programs currently executing.'):
        doc = {'course_id': 42, 'document_key': key, 'canvas_id': 1, 'title': 'Lecture',
               'source_type': 'file', 'text': text, 'status': 'ok', 'version': '1', 'pages': []}
        self.store.save_document(42, doc, 'first')
        ensure_course_chunks(42, self.store)
    def test_new_sync_queues_then_worker_indexes_and_retrieval_can_search(self):
        data = snapshot()
        data['file_inventory'] = []
        with patch.object(ingestion_service, 'sync_course', side_effect=lambda *a, **kw: copy.deepcopy(data)):
            ingestion_service.ingest_course(42, store=self.store)
        self.assertEqual(self.store.load_index_job(42)['status'], 'queued')
        self.assertEqual(process_jobs(self.store, provider=self.provider)[0]['generated'], 2)
        result = search_course(42, 'explain lecture', store=self.store, provider=self.provider)
        self.assertEqual(result['status'], 'ready')
        self.assertTrue(result['results'])
    def test_unchanged_sync_zero_calls_changed_document_only_new_text(self):
        self.save(); queue_course(42, store=self.store)
        self.assertEqual(process_jobs(self.store, provider=self.provider)[0]['generated'], 1)
        self.provider.calls.clear()
        self.save(); queue_course(42, store=self.store)
        self.assertEqual(process_jobs(self.store, provider=self.provider), [])
        self.assertEqual(self.provider.calls, [])
        self.save('file:2', 'Matrices represent linear transformations.')
        queue_course(42, store=self.store); process_jobs(self.store, provider=self.provider)
        self.assertEqual(self.provider.calls, [['Matrices represent linear transformations.']])
        self.save('file:2', 'New matrix instructions.')
        queue_course(42, store=self.store); process_jobs(self.store, provider=self.provider)
        self.assertEqual(self.provider.calls[-1], ['New matrix instructions.'])
        self.assertEqual(index_status(42, store=self.store)['status'], 'ready')
    def test_partial_failure_marks_only_failed_batch_keeps_success_and_retries_missing(self):
        for n in range(3): self.save(f'file:{n}', f'Process scheduling lecture number {n}.')
        self.provider.fail_on = 2
        queue_course(42, store=self.store); process_jobs(self.store, provider=self.provider, batch_size=1)
        status = index_status(42, store=self.store)
        self.assertEqual((status['indexed_chunks'], status['failed_chunks'], status['pending_chunks']), (1, 1, 1))
        self.assertNotIn('private', json.dumps(status))
        self.provider.fail_on = None; self.provider.calls.clear()
        queue_course(42, store=self.store, manual=True)
        process_jobs(self.store, provider=self.provider, batch_size=1)
        self.assertEqual(len(self.provider.calls), 2)
        self.assertEqual(index_status(42, store=self.store)['status'], 'ready')
    def test_failed_automatic_job_does_not_retry_on_every_poll_or_sync(self):
        self.save(); self.provider.fail_on = 1
        queue_course(42, store=self.store); process_jobs(self.store, provider=self.provider)
        queue_course(42, store=self.store); process_jobs(self.store, provider=self.provider)
        self.assertEqual(len(self.provider.calls), 1)
    def test_restart_recovers_running_and_missing_legacy_inputs_without_rebuilding_cache(self):
        self.save(); queue_course(42, store=self.store)
        job = self.store.load_index_job(42); job['status'] = 'running'; self.store.save_index_job(42, job)
        reopened = IngestionStore('student', self.store.path)
        recover_jobs(reopened); process_jobs(reopened, provider=self.provider)
        self.assertEqual(index_status(42, store=reopened)['status'], 'ready')
        self.save('file:2', 'A newly saved passage from a previous session.')
        recover_jobs(reopened)
        self.assertEqual(process_jobs(reopened, provider=self.provider)[0]['generated'], 1)
    def test_automatic_off_persists_and_manual_update_works(self):
        self.store.update_settings({'auto_index': False})
        self.save(); queue_course(42, store=self.store); recover_jobs(self.store)
        self.assertEqual(process_jobs(self.store, provider=self.provider), [])
        self.assertEqual(self.provider.calls, [])
        self.assertEqual(index_status(42, store=self.store)['status'], 'pending')
        reopened = IngestionStore('student', self.store.path)
        self.assertFalse(reopened.load_settings()['auto_index'])
        queue_course(42, store=reopened, manual=True); process_jobs(reopened, provider=self.provider)
        self.assertEqual(index_status(42, store=reopened)['status'], 'ready')
    def test_startup_queue_keeps_last_known_courses_for_index_recovery(self):
        self.save()
        self.store.queue_sync('startup')
        self.assertEqual(self.store.saved_current_course_ids(), [42])
        recover_jobs(self.store)
        self.assertEqual(self.store.load_index_job(42)['status'], 'queued')
        process_jobs(self.store, provider=self.provider)
        self.assertEqual(index_status(42, store=self.store)['status'], 'ready')
        # A genuinely empty successful Canvas course list must not resurrect old courses.
        self.store.save_sync_state({'status':'completed','total_courses':0,'courses':[],'previous_course_ids':[42]})
        self.assertEqual(self.store.saved_current_course_ids(), [])
    def test_in_progress_ingestion_does_not_claim_complete_search_coverage(self):
        self.save()
        self.store.save_course({'course_id':42,'synced_at':'first','ingestion_status':'running'})
        self.assertEqual(index_status(42, store=self.store)['status'], 'syncing')
        recover_jobs(self.store)
        self.assertEqual(self.store.load_index_job(42), {})
    def test_switching_off_pauses_automatic_queue_but_not_manual_queue(self):
        self.save(); queue_course(42, store=self.store)
        self.store.update_settings({'auto_index': False})
        process_jobs(self.store, provider=self.provider)
        self.assertEqual(self.provider.calls, [])
        self.assertEqual(self.store.load_index_job(42)['status'], 'paused')
        queue_course(42, store=self.store, manual=True); process_jobs(self.store, provider=self.provider)
        self.assertEqual(len(self.provider.calls), 1)
    def test_duplicate_inputs_keep_both_document_statuses_without_double_embedding(self):
        self.save('file:1'); self.save('file:2'); queue_course(42, store=self.store)
        report = process_jobs(self.store, provider=self.provider)[0]
        self.assertEqual(report['generated'], 1)
        status = index_status(42, store=self.store)
        self.assertEqual((status['eligible_chunks'], status['unique_inputs']), (2, 1))
        self.assertTrue(all(doc['indexed_chunks'] == 1 for doc in status['documents']))
    def test_account_and_course_settings_jobs_and_counts_are_isolated(self):
        self.save(); queue_course(42, store=self.store)
        other = IngestionStore('other', self.store.path)
        other.update_settings({'auto_index': False})
        self.assertTrue(self.store.load_settings()['auto_index'])
        self.assertEqual(other.index_jobs(), [])
        with self.assertRaises(ValueError): index_status(42, store=other)
        with self.assertRaises(ValueError): index_status(43, store=self.store)
    def test_removed_or_ineligible_text_is_not_reported_searchable(self):
        self.save(); queue_course(42, store=self.store); process_jobs(self.store, provider=self.provider)
        self.store.save_document(42, {'course_id':42, 'document_key':'file:1', 'title':'Lecture', 'status':'ok',
                                     'source_type':'file', 'text':'25', 'pages':[{'page_number':26,'text':'25','status':'ok'}]}, 'first')
        ensure_course_chunks(42, self.store)
        status = index_status(42, store=self.store)
        self.assertEqual((status['status'], status['indexed_chunks']), ('empty', 0))
    def test_precise_partial_status_and_per_document_counts(self):
        chunks = [{'text_hash': str(i), 'document_key': 'file:new' if i >= 1199 else 'file:old'} for i in range(1203)]
        with patch.object(self.store, 'load_chunks', return_value=chunks), \
             patch.object(self.store, 'load_embeddings', return_value={str(i): [] for i in range(1199)}), \
             patch.object(self.store, 'load_documents', return_value=[{'document_key': 'file:new', 'title': 'New document'}]):
            status = index_status(42, store=self.store)
        self.assertEqual((status['indexed_chunks'], status['remaining_chunks']), (1199, 4))
        self.assertEqual((status['documents'][0]['indexed_chunks'], status['documents'][0]['remaining_chunks']), (0, 4))
    def test_background_worker_drains_index_jobs_after_sync(self):
        self.save(); queue_course(42, store=self.store)
        with patch.object(course_sync_service, 'ingest_current_courses', return_value={'courses': [{'course_id': 42}], 'status': 'completed'}), \
             patch.object(course_sync_service, 'process_jobs', side_effect=lambda store: process_jobs(store, provider=self.provider)) as process, \
             patch.object(course_sync_service.time, 'sleep', side_effect=KeyboardInterrupt):
            course_sync_service.run_worker(self.store, 60)
        self.assertGreaterEqual(process.call_count, 1)
        self.assertEqual(index_status(42, store=self.store)['status'], 'ready')


    def test_disabling_during_a_batch_stops_later_calls_and_can_resume(self):
        for n in range(3): self.save(f'file:{n}', f'A different process lecture {n}.')
        original = self.provider.embed_texts
        def embed(texts):
            result = original(texts)
            self.store.update_settings({'auto_index': False})
            return result
        self.provider.embed_texts = embed
        queue_course(42, store=self.store)
        process_jobs(self.store, provider=self.provider, batch_size=1)
        self.assertEqual(len(self.provider.calls), 1)
        self.assertEqual(index_status(42, store=self.store)['indexed_chunks'], 1)
        self.provider.embed_texts = original
        self.store.update_settings({'auto_index': True})
        recover_jobs(self.store); process_jobs(self.store, provider=self.provider)
        self.assertEqual(index_status(42, store=self.store)['status'], 'ready')

    def test_new_model_configuration_queues_a_separate_set_and_reuses_its_own_cache(self):
        from dataclasses import replace
        self.save(); queue_course(42, store=self.store)
        process_jobs(self.store, provider=self.provider)
        other = Provider(); other.configuration = replace(other.configuration, version='new-version')
        queue_course(42, store=self.store, configuration=other.configuration)
        self.assertEqual(index_status(42, store=self.store, configuration=other.configuration)['remaining_chunks'], 1)
        process_jobs(self.store, provider=other)
        self.assertEqual(index_status(42, store=self.store, configuration=other.configuration)['status'], 'ready')
        self.assertEqual(len(self.store.load_embeddings(42, self.provider.configuration)), 1)
        queue_course(42, store=self.store, configuration=other.configuration)
        self.assertEqual(process_jobs(self.store, provider=other), [])


class IndexApiTests(unittest.TestCase):
    def test_off_to_on_immediately_queues_pending_inputs_without_a_canvas_sync(self):
        with tempfile.TemporaryDirectory() as directory:
            store = IngestionStore('toggle-student', Path(directory) / 'test.sqlite3')
            store.save_course({'course_id':42,'synced_at':'first'})
            store.save_sync_state({'status':'completed','courses':[{'course_id':42}]})
            store.update_settings({'auto_index':False,'monthly_warning_usd':0})
            store.save_document(42, {'document_key':'file:1','course_id':42,'source_type':'file',
                                'title':'New lecture','status':'ok','text':'Processes are programs that are executing.','pages':[]}, 'first')
            ensure_course_chunks(42, store)
            queue_course(42,store=store)
            self.assertEqual(store.index_jobs(), [])
            provider = Provider()
            service = Mock()
            service.ensure_worker.side_effect = lambda: process_jobs(store,provider=provider)
            with patch.object(main,'default_store',return_value=store), \
                 patch.object(main.app.state,'course_sync',service,create=True):
                code, data = asyncio.run(request('PATCH','/api/search/settings',{'auto_index':True}))
            self.assertEqual(code, 200)
            self.assertTrue(data['settings']['auto_index'])
            self.assertTrue(data['usage']['warning_reached'])
            service.ensure_worker.assert_called_once()
            self.assertEqual(len(provider.calls), 1)
            self.assertEqual(index_status(42,store=store)['status'], 'ready')
            self.assertEqual(store.load_sync_state()['status'], 'completed')

    def test_status_and_update_are_thin_and_worker_is_woken(self):
        status = {'eligible_chunks': 1203, 'indexed_chunks': 1199, 'remaining_chunks': 4}
        with patch.object(main.search_index_service, 'index_status', return_value=status):
            code, data = asyncio.run(request('GET', '/api/courses/42/search-index'))
            self.assertEqual((code, data['remaining_chunks']), (200, 4))
        service = Mock()
        main.app.state.course_sync = service
        with patch.object(main.search_index_service, 'queue_course', return_value=status):
            self.assertEqual(asyncio.run(request('POST', '/api/courses/42/search-index'))[0], 202)
        service.ensure_worker.assert_called_once()
    def test_invalid_course_no_index_and_errors_are_safe(self):
        with patch.object(main.search_index_service, 'index_status', side_effect=ValueError('private')):
            self.assertEqual(asyncio.run(request('GET', '/api/courses/42/search-index'))[0], 404)
            self.assertEqual(asyncio.run(request('GET', '/api/courses/0/search-index'))[0], 422)
    def test_settings_validation_and_persistence(self):
        store = Mock()
        store.load_api_usage.return_value = []
        store.saved_current_course_ids.return_value = []
        store.load_settings.return_value = {'auto_index': False, 'monthly_warning_usd': 5}
        store.update_settings.return_value = store.load_settings.return_value
        with patch.object(main, 'default_store', return_value=store):
            code, data = asyncio.run(request('PATCH', '/api/search/settings', {'auto_index': False, 'monthly_warning_usd': 5}))
            self.assertEqual(code, 200)
            self.assertEqual(data['settings']['monthly_warning_usd'], 5)
            for value in (-1, 'NaN', 'inf'):
                self.assertEqual(asyncio.run(request('PATCH', '/api/search/settings', {'monthly_warning_usd': value}))[0], 422)
            self.assertEqual(asyncio.run(request('PATCH', '/api/search/settings', {'auto_index': 'yes'}))[0], 422)

