"""Provider accounting is local, scoped, exact-once per response, and secrets-free."""
import asyncio
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from backend.app.api_usage_service import UsageRecorder, usage_summary
from backend.app.ingestion_store import IngestionStore
from backend.app.embedding_service import EmbeddingConfiguration, OpenAIEmbeddingProvider
from backend.app.llm_service import OpenAIAnswerProvider
from backend.app.vision_service import OpenAIVisionExtractionProvider


class UsageTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(); self.addCleanup(directory.cleanup)
        self.store = IngestionStore('student', Path(directory.name) / 'test.sqlite3')
        self.recorder = UsageRecorder(self.store)
    def record(self, identity, feature='study_assistant', model='gpt-6-luna', tokens=None):
        self.recorder.record({'id': identity, 'usage': tokens or {'input_tokens': 100, 'output_tokens': 20}},
                             feature=feature, operation='test', model=model)
    def test_categories_and_repeated_response_not_counted_twice(self):
        self.record('e', 'embedding', 'text-embedding-3-small', {'prompt_tokens': 1000})
        self.record('e', 'embedding', 'text-embedding-3-small', {'prompt_tokens': 1000})
        self.record('v', 'vision'); self.record('a'); self.record('q', 'query_understanding')
        result = usage_summary(self.store)
        self.assertEqual(result['total']['calls'], 4)
        self.assertEqual(result['embedding']['calls'], 1)
        self.assertEqual(result['vision']['calls'], 1)
        self.assertEqual(result['assistant_other']['calls'], 2)
        self.assertAlmostEqual(result['embedding']['estimated_cost_usd'], 0.00002)
        self.assertAlmostEqual(sum(result[k]['estimated_cost_usd'] for k in ('embedding','vision','assistant_other')),
                               result['total']['estimated_cost_usd'])
    def test_account_isolation_restart_and_unknown_pricing(self):
        self.record('unknown', model='unknown-model')
        result = usage_summary(IngestionStore('student', self.store.path))
        self.assertEqual(result['total']['unpriced_calls'], 1)
        self.assertEqual(usage_summary(IngestionStore('other', self.store.path))['total']['calls'], 0)
        self.assertIsNone(self.store.load_api_usage()[0]['estimated_cost_usd'])
    def test_cached_input_discount_missing_usage_and_warning(self):
        self.record('cached', tokens={'input_tokens': 100, 'output_tokens': 20, 'input_tokens_details': {'cached_tokens': 80}})
        self.assertAlmostEqual(self.store.load_api_usage()[0]['estimated_cost_usd'], (20*.1+80*.01+20*.5)/1e6)
        self.recorder.record({}, feature='vision', operation='failed', model='gpt-6-luna')
        self.store.update_settings({'monthly_warning_usd': 0})
        result = usage_summary(self.store)
        self.assertTrue(result['warning_reached'])
        self.assertEqual(result['total']['unpriced_calls'], 1)
        self.assertNotIn('key', json.dumps(self.store.load_api_usage()).lower())
    def test_today_and_month_exclude_older_calls(self):
        self.record('now')
        with self.store._connection() as con:
            con.execute("UPDATE api_usage SET recorded_at='2020-01-01T00:00:00+00:00'")
        result = usage_summary(self.store)
        self.assertEqual(result['today']['calls'], 0)
        self.assertEqual(result['this_month']['calls'], 0)
        self.assertEqual(result['total']['calls'], 1)
    def test_monthly_feature_breakdown_excludes_older_tracked_calls(self):
        self.record('older', 'embedding', 'text-embedding-3-small', {'prompt_tokens':1000})
        self.record('new', 'embedding', 'text-embedding-3-small', {'prompt_tokens':1000})
        self.record('vision', 'vision')
        self.record('assistant')
        with self.store._connection() as connection:
            connection.execute("UPDATE api_usage SET recorded_at='2030-06-15T12:00:00+00:00'")
            connection.execute("UPDATE api_usage SET recorded_at='2029-01-01T12:00:00+00:00' WHERE operation_id='older'")
        result = usage_summary(self.store, now=datetime(2030,6,20,tzinfo=timezone.utc))
        month = result['monthly_breakdown']
        self.assertEqual(result['embedding']['calls'], 2)
        self.assertEqual(month['embedding']['calls'], 1)
        self.assertEqual(sum(value['calls'] for value in month.values()), result['this_month']['calls'])
        self.assertAlmostEqual(sum(value['estimated_cost_usd'] for value in month.values()), result['this_month']['estimated_cost_usd'])
        self.assertIn('authoritative account total', result['notice'])
    def test_real_provider_boundaries_record_embeddings_and_answer_once(self):
        response = Mock(status_code=200, headers={'x-request-id': 'embedding-request'})
        response.json.return_value = {'model':'text-embedding-3-small', 'data':[{'index':0,'embedding':[1,0,0]}], 'usage': {'prompt_tokens': 7}}
        provider = OpenAIEmbeddingProvider(EmbeddingConfiguration(dimensions=3), api_key='fake', usage_recorder=self.recorder)
        with patch('backend.app.embedding_service.requests.post', return_value=response): provider.embed_texts(['course text'])
        response.json.return_value = {'id':'answer-response','status':'completed', 'output':[{'type':'message','role':'assistant','content':[{'type':'output_text','text':json.dumps({'status':'answered','blocks':[],'limitations':[]})}]}], 'usage':{'input_tokens':4,'output_tokens':5}}
        provider = OpenAIAnswerProvider(api_key='fake', usage_recorder=self.recorder)
        with patch('backend.app.llm_service.requests.post', return_value=response): provider.generate('rules',42,'question',[])
        self.assertEqual([row['feature'] for row in self.store.load_api_usage()], ['embedding','study_assistant'])
    def test_real_vision_boundary_records_call_but_validation_can_still_fail(self):
        response = SimpleNamespace(id='vision-response', status='completed', output_text=json.dumps({'complete':True,'unreadable':[],'elements':[{'type':'text','text':'Visible diagram labels'}]}), usage=SimpleNamespace(input_tokens=10,output_tokens=5))
        client = Mock(); client.__enter__ = Mock(return_value=client); client.__exit__ = Mock(return_value=False)
        client.responses.create.return_value = response
        with patch('openai.OpenAI', return_value=client):
            provider = OpenAIVisionExtractionProvider(api_key='fake', usage_recorder=self.recorder)
            provider.extract_page(b'\x89PNG\r\n\x1a\nfake', native_text='')
        self.assertEqual(self.store.load_api_usage()[0]['feature'], 'vision')
    def test_usage_survives_material_cleanup(self):
        self.record('retained')
        self.store.save_course({'course_id':42,'course':{'metadata':{}},'synced_at':'first'})
        with self.store._connection() as con: self.store.remove_course_materials(con, [42])
        self.assertEqual(usage_summary(self.store)['total']['calls'], 1)

    def test_query_understanding_usage_is_recorded(self):
        from unittest.mock import AsyncMock
        from backend.app.query_understanding_service import empty_understanding
        response = SimpleNamespace(id='query-response', status='completed',
            output_text=json.dumps(empty_understanding('Explain processes')),
            usage=SimpleNamespace(input_tokens=12,output_tokens=8))
        with patch('openai.AsyncOpenAI') as factory:
            factory.return_value.__aenter__.return_value.responses.create = AsyncMock(return_value=response)
            provider = OpenAIAnswerProvider(api_key='fake', usage_recorder=self.recorder)
            asyncio.run(provider.rewrite_query(42,'Explain processes',{}))
        self.assertEqual(self.store.load_api_usage()[0]['feature'], 'query_understanding')

    def test_stream_records_final_usage_only_once_and_preserves_events(self):
        payload = json.dumps({'status':'answered','blocks':[{'text':'A process executes a program.','source_ids':['S1']}],'limitations':[]})
        response = SimpleNamespace(id='stream-response',status='completed',usage=SimpleNamespace(input_tokens=20,output_tokens=10))
        class Stream:
            async def __aenter__(self): return self
            async def __aexit__(self,*a): pass
            async def __aiter__(self):
                yield SimpleNamespace(type='response.output_text.delta',delta=payload)
                yield SimpleNamespace(type='response.completed',response=response)
        async def run():
            with patch('openai.AsyncOpenAI') as factory:
                client = Mock()
                client.responses.stream.return_value = Stream()
                factory.return_value.__aenter__.return_value = client
                provider = OpenAIAnswerProvider(api_key='fake', usage_recorder=self.recorder)
                return [event async for event in provider.stream_generate('rules',42,'question',[{'id':'S1'}],{})]
        events = asyncio.run(run())
        self.assertEqual([event['event'] for event in events], ['answer_delta','complete'])
        self.assertEqual(usage_summary(self.store)['total']['calls'], 1)
