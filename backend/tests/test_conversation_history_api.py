"""History endpoints use temporary SQLite and never contact Canvas/OpenAI."""

import asyncio
import json
import unittest
from unittest.mock import patch

from backend.app import main
from backend.app.conversation_service import ConversationService
from backend.app.conversation_store import ConversationStore
from backend.evaluation.fixtures import EvaluationFixture


async def request(path, method='GET', payload=None):
    messages = []
    async def receive():
        return {'type':'http.request', 'body':json.dumps(payload).encode() if payload is not None else b'', 'more_body':False}
    async def send(message):
        messages.append(message)
    await main.app({'type':'http', 'asgi':{'version':'3.0','spec_version':'2.4'}, 'http_version':'1.1',
        'method':method, 'scheme':'http', 'path':path, 'raw_path':path.encode(), 'query_string':b'',
        'root_path':'', 'headers':[(b'content-type',b'application/json')] if payload is not None else [], 'server':('test',80), 'client':('test',1)},receive,send)
    return next(m['status'] for m in messages if m['type']=='http.response.start'), json.loads(b''.join(m.get('body',b'') for m in messages if m['type']=='http.response.body'))


class HistoryAPITests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.fixture = EvaluationFixture();self.addCleanup(self.fixture.close)
        self.repo = ConversationStore(self.fixture.store.path)
        self.service = ConversationService(repository=self.repo)
        patch.object(main,'conversations',self.service).start()
        patch.object(main,'default_store',return_value=self.fixture.store).start()
        self.addCleanup(patch.stopall)
        self.base = '/api/courses/42/conversations'

    async def test_create_list_and_reopen_independent_threads(self):
        self.assertEqual(await request(self.base),(200,[]))
        code, first = await request(self.base,'POST')
        self.assertEqual(code,201)
        _, second = await request(self.base,'POST')
        self.assertNotEqual(first['conversation_id'],second['conversation_id'])
        self.assertEqual((await request(self.base))[1][0]['conversation_id'],second['conversation_id'])
        _, reopened = await request(self.base+'/'+first['conversation_id'])
        self.assertEqual(reopened['messages'],[])
        self.assertEqual((await request(self.base))[1][0]['conversation_id'],first['conversation_id'])

    async def test_wrong_account_and_course_do_not_expose_thread(self):
        _, first = await request(self.base,'POST')
        self.fixture.store.save_course({'course_id':43,'synced_at':'fixed'})
        self.assertEqual((await request('/api/courses/43/conversations/'+first['conversation_id']))[0],404)
        self.fixture.store.namespace = 'other'
        self.fixture.store.save_course({'course_id':42,'synced_at':'fixed'})
        self.assertEqual(await request(self.base),(200,[]))
        self.assertEqual((await request(self.base+'/'+first['conversation_id']))[0],404)

    async def test_invalid_and_missing_course_are_rejected(self):
        self.assertEqual((await request('/api/courses/0/conversations'))[0],422)
        self.assertEqual((await request('/api/courses/99/conversations','POST'))[0],404)
        self.assertEqual((await request(self.base+'/bad-id'))[0],422)

    async def test_storage_failure_is_safe(self):
        with patch.object(self.repo,'list',side_effect=OSError('private machine details')):
            code,body = await request(self.base)
        self.assertEqual(code,503)
        self.assertNotIn('private',json.dumps(body))
