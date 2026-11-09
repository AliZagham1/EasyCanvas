"""Rename/delete extend existing SQLite threads without touching course data."""

import sqlite3
import unittest
from contextlib import closing

from backend.app.conversation_store import ConversationStore, validate_title
from backend.app.conversation_service import ConversationService, ConversationError
from backend.app.query_understanding_service import empty_understanding
from backend.evaluation.fixtures import EvaluationFixture, ReplayProvider


class ConversationManagementTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.fixture=EvaluationFixture();self.addCleanup(self.fixture.close)
        self.store=self.fixture.store
        self.repo=ConversationStore(self.store.path)
        self.service=ConversationService(repository=self.repo)
        self.namespace=self.store.namespace

    async def create(self,title='HW1'):
        session=self.service.acquire(self.namespace,42)
        q='Explain '+title
        provider=ReplayProvider(empty_understanding(q,intent='explain_assignment',assignment_reference=title))
        events=[e async for e in self.service.events(session,q,'first',store=self.store,provider=provider,embedding_provider=self.fixture.embedding)]
        self.assertEqual(events[-1]['data']['status'],'answered')
        return session

    async def test_rename_preserves_id_course_messages_citations_and_state_after_restart(self):
        a=await self.create()
        before=self.repo.load(self.namespace,42,a.id)
        result=self.repo.rename(self.namespace,42,a.id,'  Homework review  ')
        after=ConversationStore(self.store.path).load(self.namespace,42,a.id)
        self.assertEqual(result['title'],'Homework review')
        self.assertEqual(after['conversation_id'],before['conversation_id'])
        self.assertEqual(after['course_id'],before['course_id'])
        self.assertEqual(after['created_at'],before['created_at'])
        self.assertEqual(after['messages'],before['messages'])
        self.assertEqual(after['state'],before['state'])
        restored=ConversationService(repository=self.repo).acquire(self.namespace,42,a.id)
        self.assertEqual(restored.active_assignments,[101])

    async def test_custom_default_looking_name_survives_next_turn(self):
        session=self.service.acquire(self.namespace,42);session.busy=False
        self.repo.rename(self.namespace,42,session.id,'New conversation')
        session=self.service.acquire(self.namespace,42,session.id)
        self.repo.begin_turn(session,'one','Do not replace my name')
        self.assertEqual(self.repo.load(self.namespace,42,session.id)['title'],'New conversation')

    async def test_rename_and_delete_wrong_account_or_course_leave_data_intact(self):
        a=await self.create();before=self.repo.load(self.namespace,42,a.id)
        for account,course in [('other',42),(self.namespace,43)]:
            self.assertIsNone(self.repo.rename(account,course,a.id,'Wrong'))
            self.assertFalse(self.service.delete(account,course,a.id))
        self.assertEqual(self.repo.load(self.namespace,42,a.id),before)

    async def test_delete_inactive_thread_does_not_change_selected_thread_or_material(self):
        a=await self.create();b=await self.create('HW10')
        before=self.repo.load(self.namespace,42,b.id)
        chunks=self.store.load_chunks(42)
        vectors=self.store.load_embeddings(42,self.fixture.embedding.configuration)
        self.assertTrue(self.service.delete(self.namespace,42,a.id))
        self.assertIsNone(self.repo.load(self.namespace,42,a.id))
        self.assertEqual(self.repo.load(self.namespace,42,b.id),before)
        self.assertEqual(self.store.load_chunks(42),chunks)
        self.assertEqual(self.store.load_embeddings(42,self.fixture.embedding.configuration),vectors)

    async def test_deleted_active_id_cannot_be_resumed_and_remaining_state_is_own(self):
        a=await self.create();b=await self.create('HW10')
        self.assertTrue(self.service.delete(self.namespace,42,b.id))
        self.assertNotIn(b.id,self.service.sessions)
        with self.assertRaises(ConversationError):self.service.acquire(self.namespace,42,b.id)
        remaining=self.repo.list(self.namespace,42)
        self.assertEqual([r['conversation_id'] for r in remaining],[a.id])
        restored=self.service.acquire(self.namespace,42,a.id)
        self.assertEqual(restored.active_assignments,[101])
        self.assertNotIn(110,restored.active_assignments)

    async def test_delete_only_thread_then_create_is_clean(self):
        a=await self.create();self.service.delete(self.namespace,42,a.id)
        self.assertEqual(self.repo.list(self.namespace,42),[])
        new=self.service.acquire(self.namespace,42)
        self.assertNotEqual(new.id,a.id)
        self.assertFalse(new.history);self.assertFalse(new.state);self.assertFalse(new.active_documents)
        with self.repo.connection() as c:
            self.assertEqual(c.execute('SELECT count(*) FROM assistant_messages WHERE namespace=? AND conversation_id=?',(self.namespace,a.id)).fetchone()[0],0)

    async def test_busy_thread_is_not_deleted(self):
        session=self.service.acquire(self.namespace,42)
        with self.assertRaises(ConversationError):self.service.delete(self.namespace,42,session.id)
        self.assertIsNotNone(self.repo.load(self.namespace,42,session.id))

    async def test_old_schema_migrates_without_losing_thread_or_course_content(self):
        with closing(sqlite3.connect(self.store.path)) as c, c:
            c.execute('CREATE TABLE assistant_conversations(namespace TEXT,conversation_id TEXT,course_id INTEGER,title TEXT,created_at TEXT,updated_at TEXT,state_json TEXT,PRIMARY KEY(namespace,conversation_id))')
            c.execute('INSERT INTO assistant_conversations VALUES (?,?,?,?,?,?,?)',(self.namespace,'old',42,'Original','created','updated','{}'))
        data=self.repo.load(self.namespace,42,'old')
        self.assertEqual(data['title'],'Original')
        self.assertEqual(data['state'],{})
        self.assertEqual(data['title_is_custom'],0)
        self.repo.rename(self.namespace,42,'old','Migrated')
        self.assertTrue(self.store.load_documents(42))

    def test_name_validation_preserves_unicode_and_rejects_invalid_names(self):
        self.assertEqual(validate_title('  数学 – révision  '),'数学 – révision')
        for bad in ('','   ','x'*101,'bad\x00name','two\nlines',3):
            with self.subTest(bad=bad),self.assertRaises(ValueError):validate_title(bad)
