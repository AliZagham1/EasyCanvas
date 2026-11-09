"""HTTP mutation validation and isolation over the same persisted threads."""

import unittest

from backend.tests import test_conversation_history_api as history


class ManagementAPITests(unittest.IsolatedAsyncioTestCase):
    setUp=history.HistoryAPITests.setUp

    async def test_patch_renames_existing_thread_and_delete_removes_only_it(self):
        _,first=await history.request(self.base,'POST')
        _,other=await history.request(self.base,'POST')
        path=self.base+'/'+first['conversation_id']
        code,renamed=await history.request(path,'PATCH',{'title':'  Renamed thread  '})
        self.assertEqual(code,200);self.assertEqual(renamed['title'],'Renamed thread')
        self.assertEqual(renamed['conversation_id'],first['conversation_id'])
        code,result=await history.request(path,'DELETE')
        self.assertEqual(code,200);self.assertTrue(result['deleted'])
        self.assertEqual((await history.request(path))[0],404)
        self.assertEqual((await history.request(self.base+'/'+other['conversation_id']))[0],200)

    async def test_invalid_names_and_unknown_ids_are_safe(self):
        _,first=await history.request(self.base,'POST')
        path=self.base+'/'+first['conversation_id']
        for title in ('','  ','x'*101,'bad\x00name',7):
            with self.subTest(title=title):self.assertEqual((await history.request(path,'PATCH',{'title':title}))[0],422)
        missing=self.base+'/00000000-0000-4000-8000-000000000000'
        self.assertEqual((await history.request(missing,'DELETE'))[0],404)
        self.assertEqual((await history.request(missing,'PATCH',{'title':'Name'}))[0],404)

    async def test_wrong_course_and_namespace_cannot_mutate_thread(self):
        _,first=await history.request(self.base,'POST')
        self.fixture.store.save_course({'course_id':43,'synced_at':'fixed'})
        wrong='/api/courses/43/conversations/'+first['conversation_id']
        self.assertEqual((await history.request(wrong,'PATCH',{'title':'Wrong'}))[0],404)
        self.assertEqual((await history.request(wrong,'DELETE'))[0],404)
        self.fixture.store.namespace='other'
        self.fixture.store.save_course({'course_id':42,'synced_at':'fixed'})
        wrong=self.base+'/'+first['conversation_id']
        self.assertEqual((await history.request(wrong,'DELETE'))[0],404)

    async def test_busy_thread_returns_safe_conflict(self):
        _,first=await history.request(self.base,'POST')
        self.service.acquire(self.fixture.store.namespace,42,first['conversation_id'])
        code,body=await history.request(self.base+'/'+first['conversation_id'],'DELETE')
        self.assertEqual(code,409)
        self.assertIn('response',body['detail'])
