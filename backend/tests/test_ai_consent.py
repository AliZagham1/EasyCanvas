"""Global AI consent: default-deny, server-side enforcement at every provider boundary, and non-AI sync. Offline only."""
import asyncio
import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from backend.tests.ai_consent_granted import REAL_IS_AI_ENABLED  # the test process normally fakes consent; undo that here

from backend.app import consent_service, ingestion_service, main, visual_document_service
from backend.app.api_usage_service import UsageRecorder
from backend.app.chunking_service import ensure_course_chunks
from backend.app.consent_service import AIConsentRequired, CONSENT_VERSION
from backend.app.conversation_service import ConversationService
from backend.app.embedding_service import EmbeddingConfiguration, OpenAIEmbeddingProvider, embed_course
from backend.app.ingestion_store import IngestionStore
from backend.app.llm_service import OpenAIAnswerProvider
from backend.app.search_index_service import index_status, process_jobs, queue_course, recover_jobs
from backend.app.vision_service import OpenAIVisionExtractionProvider
from backend.tests.test_ingestion_pipeline import snapshot
from backend.tests.test_retention_api import request


class Base(unittest.TestCase):
    def setUp(self):
        real = patch.object(consent_service, 'is_ai_enabled', REAL_IS_AI_ENABLED)
        real.start(); self.addCleanup(real.stop)
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / 'test.sqlite3'
        self.store = IngestionStore('student', self.path)
        self.store.save_course({'course_id': 42, 'synced_at': 'first'})
        self.store.save_sync_state({'courses': [{'course_id': 42}], 'status': 'completed'})
        for target in (main,):
            patcher = patch.object(target, 'default_store', lambda: IngestionStore('student', self.path))
            patcher.start(); self.addCleanup(patcher.stop)
        # Any real network call is a test failure.
        for name in ('backend.app.llm_service.requests.post', 'backend.app.embedding_service.requests.post'):
            net = patch(name, side_effect=AssertionError('network call without consent'))
            self.network = net.start(); self.addCleanup(net.stop)

    def grant(self, decision='granted'):
        return consent_service.record_decision(self.store, decision, CONSENT_VERSION)

    def save_document(self, key='file:1', text='Processes are programs currently executing.'):
        self.store.save_document(42, {'course_id': 42, 'document_key': key, 'canvas_id': 1, 'title': 'Lecture',
                                      'source_type': 'file', 'text': text, 'status': 'ok', 'version': '1', 'pages': []}, 'first')
        ensure_course_chunks(42, self.store)


class FakeEmbedder:
    def __init__(self, store=None, withdraw_after=None):
        self.configuration = EmbeddingConfiguration()
        self.calls, self.store, self.withdraw_after = [], store, withdraw_after
    def embed_texts(self, texts):
        consent_service.require_ai_consent(self.store)
        self.calls.append(list(texts))
        if self.withdraw_after == len(self.calls):
            consent_service.record_decision(self.store, 'withdrawn', CONSENT_VERSION)
        return {'vectors': [[1.] + [0.] * 1535 for _ in texts], 'prompt_tokens': len(texts)}


class DefaultDeniedTests(Base):
    def test_first_time_user_has_no_decision_and_ai_is_off(self):
        view = consent_service.consent_view(self.store)
        self.assertEqual((view['status'], view['enabled'], view['needs_decision']), ('undecided', False, True))
        with self.assertRaises(AIConsentRequired):
            consent_service.require_ai_consent(self.store)

    def test_no_store_means_denied(self):
        self.assertFalse(consent_service.is_ai_enabled(None))

    def test_accepting_enables_declining_does_not_and_neither_asks_again(self):
        self.assertTrue(self.grant()['enabled'])
        view = self.grant('declined')
        self.assertEqual((view['enabled'], view['needs_decision']), (False, False))

    def test_decision_survives_a_restart(self):
        self.grant()
        self.assertTrue(consent_service.is_ai_enabled(IngestionStore('student', self.path)))
        self.grant('withdrawn')
        self.assertFalse(consent_service.is_ai_enabled(IngestionStore('student', self.path)))

    def test_missing_or_corrupt_records_are_denied(self):
        for bad in ('garbage', 7, [], {}, {'status': 'granted'}, {'status': 'yes', 'version': CONSENT_VERSION},
                    {'status': 'granted', 'version': '1'}, {'status': 'granted', 'version': True}, {'status': 'granted', 'version': None}):
            self.store.update_settings({'ai_consent': bad})
            self.assertFalse(consent_service.is_ai_enabled(self.store), bad)
        self.store.update_settings({'ai_consent': {'status': 'granted', 'version': CONSENT_VERSION}})
        self.assertTrue(consent_service.is_ai_enabled(self.store))

    def test_unreadable_database_is_denied(self):
        self.grant()
        broken = Mock(load_ai_consent=Mock(side_effect=OSError))
        self.assertFalse(consent_service.is_ai_enabled(broken))

    def test_older_consent_version_is_denied_and_asks_again(self):
        self.store.update_settings({'ai_consent': {'status': 'granted', 'version': CONSENT_VERSION - 1, 'decided_at': 'x'}})
        view = consent_service.consent_view(self.store)
        self.assertEqual((view['enabled'], view['needs_decision']), (False, True))

    def test_one_consent_covers_courses_added_later(self):
        self.grant()
        self.store.save_course({'course_id': 99, 'synced_at': 'later'})
        self.assertTrue(consent_service.is_ai_enabled(self.store))
        self.assertNotIn('course', str(self.store.load_ai_consent()))


class ConsentApiTests(Base):
    def test_get_and_put(self):
        code, body = asyncio.run(request('GET', '/api/ai-consent'))
        self.assertEqual((code, body['status'], body['needs_decision'], body['current_version']), (200, 'undecided', True, CONSENT_VERSION))
        code, body = asyncio.run(request('PUT', '/api/ai-consent', {'decision': 'granted', 'version': CONSENT_VERSION}))
        self.assertEqual((code, body['enabled']), (200, True))
        self.assertTrue(consent_service.is_ai_enabled(self.store))

    def test_bad_decisions_are_rejected_and_save_nothing(self):
        self.assertEqual(asyncio.run(request('PUT', '/api/ai-consent', {'decision': 'maybe', 'version': CONSENT_VERSION}))[0], 422)
        self.assertEqual(asyncio.run(request('PUT', '/api/ai-consent', {'decision': 'granted', 'version': CONSENT_VERSION + 1}))[0], 409)
        self.assertEqual(asyncio.run(request('PUT', '/api/ai-consent', {'decision': 'granted', 'version': CONSENT_VERSION, 'courses': [1]}))[0], 422)
        self.assertFalse(consent_service.is_ai_enabled(self.store))

    def test_consent_cannot_be_granted_through_the_search_settings_endpoint(self):
        code, _ = asyncio.run(request('PATCH', '/api/search/settings', {'ai_consent': {'status': 'granted', 'version': CONSENT_VERSION}}))
        self.assertEqual(code, 422)
        self.assertFalse(consent_service.is_ai_enabled(self.store))

    def test_ai_routes_are_403_with_a_stable_code_and_do_no_work(self):
        with patch.object(main.rag_service, 'answer_question') as answer, \
             patch.object(main.conversations, 'acquire') as acquire, \
             patch.object(main.openai_key_service, 'verify_key') as verify:
            for method, path, payload in (
                    ('POST', '/api/courses/42/assistant', {'question': 'What is a process?'}),
                    ('POST', '/api/courses/42/assistant/stream', {'question': 'What is a process?', 'turn_id': '00000000-0000-4000-8000-000000000001'}),
                    ('POST', '/api/courses/42/search-index', None),
                    ('PUT', '/api/openai-key', {'api_key': 'sk-' + 'a' * 30})):
                code, body = asyncio.run(request(method, path, payload))
                self.assertEqual((code, body.get('code')), (403, 'ai_consent_required'), path)
            answer.assert_not_called(); acquire.assert_not_called(); verify.assert_not_called()
        self.assertEqual(self.store.index_jobs(), [])

    def test_a_service_level_denial_is_403_not_a_provider_error(self):
        self.grant()
        with patch.object(main.rag_service, 'answer_question', side_effect=AIConsentRequired()):
            code, body = asyncio.run(request('POST', '/api/courses/42/assistant', {'question': 'What is a process?'}))
        self.assertEqual((code, body['code']), (403, 'ai_consent_required'))

    def test_withdrawn_consent_blocks_routes_again(self):
        self.grant(); self.grant('withdrawn')
        code, body = asyncio.run(request('POST', '/api/courses/42/assistant', {'question': 'What is a process?'}))
        self.assertEqual((code, body['code']), (403, 'ai_consent_required'))

    def test_non_ai_routes_work_without_consent(self):
        with patch.object(main, 'get_current_courses', return_value=[]) as courses:
            self.assertEqual(asyncio.run(request('GET', '/api/courses'))[0], 200)
            courses.assert_called_once()
        self.assertEqual(asyncio.run(request('GET', '/api/ai-consent'))[0], 200)
        self.assertEqual(asyncio.run(request('GET', '/api/schedule'))[0], 200)


class ProviderBoundaryTests(Base):
    """Direct provider use cannot bypass consent, even with a valid-looking key."""
    def recorder(self):
        return UsageRecorder(self.store)

    def test_providers_cannot_be_built_without_consent(self):
        with self.assertRaises(AIConsentRequired):
            OpenAIAnswerProvider(api_key='sk-fake', usage_recorder=self.recorder())
        with self.assertRaises(AIConsentRequired):
            OpenAIEmbeddingProvider(api_key='sk-fake', usage_recorder=self.recorder())
        with self.assertRaises(AIConsentRequired):
            OpenAIVisionExtractionProvider(api_key='sk-fake', usage_recorder=self.recorder())
        with self.assertRaises(AIConsentRequired):  # no store at all: unknown means denied
            OpenAIAnswerProvider(api_key='sk-fake', usage_recorder=False)

    def test_withdrawal_after_construction_blocks_every_provider_call(self):
        self.grant()
        answer = OpenAIAnswerProvider(api_key='sk-fake', usage_recorder=self.recorder())
        embed = OpenAIEmbeddingProvider(api_key='sk-fake', usage_recorder=self.recorder())
        vision = OpenAIVisionExtractionProvider(api_key='sk-fake', usage_recorder=self.recorder())
        self.grant('withdrawn')
        with patch('openai.AsyncOpenAI') as async_client, patch('openai.OpenAI') as client:
            with self.assertRaises(AIConsentRequired):
                answer.generate('rules', 42, 'q', [])
            with self.assertRaises(AIConsentRequired):
                answer.generate_general('rules', 'q')
            with self.assertRaises(AIConsentRequired):
                asyncio.run(answer.rewrite_query(42, 'q', {}))
            async def drain(stream):
                async for _ in stream:
                    pass
            with self.assertRaises(AIConsentRequired):
                asyncio.run(drain(answer.stream_generate('rules', 42, 'q', [], {})))
            with self.assertRaises(AIConsentRequired):
                asyncio.run(drain(answer.stream_general('rules', 'q')))
            with self.assertRaises(AIConsentRequired):
                embed.embed_texts(['hello'])
            with self.assertRaises(AIConsentRequired):
                vision.extract_page(b'\x89PNG\r\n\x1a\nxx', native_text='')
            async_client.assert_not_called(); client.assert_not_called()
        self.network.assert_not_called()

    def test_consent_error_is_not_swallowed_as_a_fallback_or_retry(self):
        self.grant()
        answer = OpenAIAnswerProvider(api_key='sk-fake', usage_recorder=self.recorder())
        self.grant('withdrawn')
        from backend.app import query_understanding_service
        with self.assertRaises(AIConsentRequired):
            asyncio.run(query_understanding_service.understand_question(42, 'what is due?', store=self.store, provider=answer))

    def test_a_stream_stops_at_the_next_event_after_withdrawal(self):
        self.grant()
        watch = consent_service.ConsentWatch(self.store, interval=0)
        watch.check()
        self.grant('withdrawn')
        with self.assertRaises(AIConsentRequired):
            watch.check()

    def test_conversation_turn_reports_a_consent_error_event_not_a_retrieval_error(self):
        service = ConversationService()
        session = service.acquire(self.store.namespace, 42)
        async def run():
            return [event async for event in service.events(session, 'What is a process?', '00000000-0000-4000-8000-000000000001', store=self.store)]
        events = asyncio.run(run())
        self.assertEqual(events[-1]['event'], 'error')
        self.assertEqual(events[-1]['data']['code'], 'ai_consent_required')
        self.assertNotIn('insufficient', str(events[-1]['data']).lower())

    def test_vision_is_off_without_consent_even_if_enabled_in_settings(self):
        self.store.update_settings({'vision_enabled': True})
        self.assertFalse(visual_document_service.vision_enabled(self.store))
        self.grant()
        self.assertTrue(visual_document_service.vision_enabled(self.store))
        self.grant('withdrawn')
        self.assertFalse(visual_document_service.vision_enabled(self.store))


class BackgroundIndexingTests(Base):
    def test_nothing_is_queued_or_processed_without_consent(self):
        self.save_document()
        self.assertEqual(queue_course(42, store=self.store, manual=True)['job_status'], 'idle')
        self.assertEqual(self.store.index_jobs(), [])
        recover_jobs(self.store)
        self.assertEqual(self.store.index_jobs(), [])
        # A job that was queued earlier is paused, not run.
        self.grant(); queue_course(42, store=self.store)
        self.grant('withdrawn')
        embedder = FakeEmbedder(self.store)
        self.assertEqual(process_jobs(self.store, provider=embedder), [])
        self.assertEqual(embedder.calls, [])
        self.assertEqual(self.store.load_index_job(42)['status'], 'paused')

    def test_withdrawal_mid_indexing_stops_at_the_batch_boundary_and_regrant_resumes_without_duplicates(self):
        for number in range(3):
            self.save_document(f'file:{number}', f'Distinct lecture text number {number}. ' * 40)
        self.grant(); queue_course(42, store=self.store)
        first = FakeEmbedder(self.store, withdraw_after=1)
        process_jobs(self.store, provider=first, batch_size=1)
        self.assertEqual(len(first.calls), 1)
        self.assertEqual(self.store.load_index_job(42)['status'], 'paused')
        done = index_status(42, store=self.store)['indexed_chunks']
        self.assertEqual(done, 1)
        # Re-enable: queue only what is missing.
        self.grant()
        queue_course(42, store=self.store)
        second = FakeEmbedder(self.store)
        process_jobs(self.store, provider=second, batch_size=32)
        sent = {text for call in second.calls for text in call} & {text for call in first.calls for text in call}
        self.assertEqual(sent, set())
        self.assertEqual(index_status(42, store=self.store)['status'], 'ready')
        # Fully indexed: another pass makes no calls at all.
        third = FakeEmbedder(self.store)
        queue_course(42, store=self.store); process_jobs(self.store, provider=third)
        self.assertEqual(third.calls, [])

    def test_embed_course_with_a_real_provider_cannot_run_without_consent(self):
        self.save_document()
        with self.assertRaises(AIConsentRequired):
            embed_course(42, store=self.store)
        self.network.assert_not_called()

    def test_courses_synced_after_consent_are_indexed_automatically(self):
        self.grant()
        self.store.save_course({'course_id': 77, 'synced_at': 'later'})
        self.store.save_sync_state({'courses': [{'course_id': 42}, {'course_id': 77}], 'status': 'completed'})
        self.store.save_document(77, {'course_id': 77, 'document_key': 'file:7', 'canvas_id': 7, 'title': 'New',
                                      'source_type': 'file', 'text': 'Brand new course material.', 'status': 'ok', 'version': '1', 'pages': []}, 'later')
        ensure_course_chunks(77, self.store)
        self.assertEqual(queue_course(77, store=self.store)['job_status'], 'queued')

    def test_granting_through_the_api_queues_missing_work_only(self):
        self.save_document()
        with patch.object(main, 'default_store', lambda: IngestionStore('student', self.path)):
            code, _ = asyncio.run(request('PUT', '/api/ai-consent', {'decision': 'granted', 'version': CONSENT_VERSION}))
        self.assertEqual(code, 200)
        self.assertEqual(self.store.load_index_job(42)['status'], 'queued')


class CanvasSyncIndependenceTests(Base):
    def test_sync_runs_and_saves_without_consent_and_calls_no_ai(self):
        data = snapshot(); data['file_inventory'] = []
        with patch.object(ingestion_service, 'sync_course', side_effect=lambda *a, **kw: copy.deepcopy(data)), \
             patch('backend.app.llm_service.OpenAIAnswerProvider.__init__', side_effect=AssertionError), \
             patch('backend.app.embedding_service.OpenAIEmbeddingProvider.__init__', side_effect=AssertionError), \
             patch('backend.app.vision_service.OpenAIVisionExtractionProvider.__init__', side_effect=AssertionError):
            result = ingestion_service.ingest_course(42, store=self.store)
        self.assertIsNotNone(self.store.load_course(42))
        self.assertTrue(result['snapshot'])
        self.assertEqual(self.store.index_jobs(), [])  # no AI indexing was queued

    def test_sync_after_consent_queues_indexing_as_before(self):
        self.grant()
        data = snapshot(); data['file_inventory'] = []
        with patch.object(ingestion_service, 'sync_course', side_effect=lambda *a, **kw: copy.deepcopy(data)):
            ingestion_service.ingest_course(42, store=self.store)
        self.assertEqual(self.store.load_index_job(42)['status'], 'queued')

    def test_priorities_do_not_need_consent(self):
        with patch.object(main.priority_service, 'get_priorities', return_value={'items': []}) as priorities:
            self.assertFalse(consent_service.is_ai_enabled(self.store))
            self.assertEqual(asyncio.run(request('GET', '/api/priorities'))[0], 200)
            priorities.assert_called_once()


if __name__ == '__main__':
    unittest.main()
