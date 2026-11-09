"""Actual conversation/citation flow for backend-owned clarification choices."""

import copy
import unittest
from unittest.mock import patch

from backend.app.conversation_service import ConversationService, ConversationError
from backend.app.query_understanding_service import empty_understanding
from backend.evaluation.fixtures import EvaluationFixture, ReplayProvider


class ClarificationFollowupTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.fixture = EvaluationFixture()
        self.addCleanup(self.fixture.close)
        self.store = self.fixture.store
        first = self.fixture.assignments[-1]
        first['metadata']['due_at'] = '2026-11-04T05:59:00Z'
        written, review = copy.deepcopy(first), copy.deepcopy(first)
        for a, aid, title, due in ((written,202,'Iteration 3 Written Deliverable','2026-11-05T05:59:00Z'),
                                  (review,203,'Iteration 3 Review','2026-11-06T05:59:00Z')):
            a.update(canvas_id=aid,title=title)
            a['metadata'].update(id=aid,name=title,due_at=due,html_url=f'https://canvas.example/courses/42/assignments/{aid}')
        self.fixture.assignments.extend((written,review))
        self.fixture.snapshot['assignments'] = self.fixture.assignments
        self.store.save_course(self.fixture.snapshot)
        self.sessions = ConversationService()
        self.provider = ReplayProvider(empty_understanding('when is iteration 3 due', intent='get_deadline',
                                       assignment_reference='Iteration 3', requested_data=['deadline']))
        self.fetch = self.enterContext(patch('backend.app.canvas_service.get_assignment',
            side_effect=lambda cid, aid: next(copy.deepcopy(a['metadata']) for a in self.fixture.assignments if a['canvas_id']==aid)))

    async def ask(self, question, sid=None):
        session = self.sessions.acquire(self.store.namespace,42,sid)
        events = [e async for e in self.sessions.events(session,question,question,store=self.store,
                   provider=self.provider,embedding_provider=self.fixture.embedding)]
        self.assertEqual(events[-1]['event'],'final',events[-1])
        return session,events[-1]['data']

    async def clarify(self):
        session, result = await self.ask('when is iteration 3 due')
        self.assertEqual(result['status'],'clarification_needed')
        self.assertEqual([a['assignment_id'] for a in session.clarification['candidates']],[201,202,203])
        self.fetch.assert_not_called()
        # Deliberately wrong model plan: short replies must bypass it, not choose Written Deliverable.
        self.provider.plan = empty_understanding('Wrong single item',intent='get_deadline',assignment_reference='Iteration 3 Written Deliverable')
        return session

    async def test_all_variants_return_every_deadline_with_separate_citations(self):
        for reply in ('all','all of them','each one','every one','all three','all three please'):
            with self.subTest(reply=reply):
                self.provider.plan = empty_understanding('when is iteration 3 due',intent='get_deadline',assignment_reference='Iteration 3')
                session = await self.clarify()
                before = self.provider.rewrite_calls
                _,result = await self.ask(reply,session.id)
                self.assertEqual(result['resolved_assignments'],[201,202,203])
                self.assertEqual(self.provider.rewrite_calls,before)
                self.assertEqual(len(result['answer_blocks']),3)
                self.assertEqual(len(result['sources']),3)
                self.assertEqual([a['due_at'] for a in result['structured_data']],
                                 ['2026-11-04T05:59:00Z','2026-11-05T05:59:00Z','2026-11-06T05:59:00Z'])
                self.assertEqual(session.clarification,{})
                self.fetch.reset_mock()

    async def test_named_and_ordinal_replies_preserve_presented_order(self):
        for reply, aid in (('presentation',201),('the presentation one',201),('written deliverable',202),
                           ('review',203),('first one',201),('the second one',202),('last one',203)):
            with self.subTest(reply=reply):
                self.provider.plan = empty_understanding('when is iteration 3 due',intent='get_deadline',assignment_reference='Iteration 3')
                session = await self.clarify()
                _,result = await self.ask(reply,session.id)
                self.assertEqual(result['resolved_assignments'],[aid])
                self.assertEqual(len(result['sources']),1)
                self.assertEqual(session.clarification,{})
                self.fetch.reset_mock()

    async def test_all_without_pending_candidates_does_not_select_arbitrary_work(self):
        _, result = await self.ask('all')
        self.assertEqual(result['status'],'clarification_needed')
        self.fetch.assert_not_called()
        self.assertEqual(self.provider.rewrite_calls,0)

    async def test_new_topic_drops_old_choices(self):
        session = await self.clarify()
        self.provider.plan = empty_understanding('when is HW 1 due',intent='get_deadline',assignment_reference='HW 1')
        _, result = await self.ask('when is HW 1 due',session.id)
        self.assertEqual(result['resolved_assignments'],[101])
        self.assertEqual(session.clarification,{})
        _, result = await self.ask('all',session.id)
        self.assertEqual(result['status'],'clarification_needed')

    async def test_choices_do_not_cross_accounts_or_courses(self):
        session = await self.clarify()
        for namespace,cid in ((self.store.namespace,43),('other',42)):
            with self.assertRaises(ConversationError):
                self.sessions.acquire(namespace,cid,session.id)

    async def test_removed_choice_requires_new_clarification(self):
        session = await self.clarify()
        snapshot = copy.deepcopy(self.fixture.snapshot)
        snapshot['assignments'] = [a for a in snapshot['assignments'] if a['canvas_id']!=203]
        self.store.save_course(snapshot)
        _,result = await self.ask('all',session.id)
        self.assertEqual(result['status'],'clarification_needed')
        self.fetch.assert_not_called()

    async def test_model_clarification_still_publishes_actual_candidate_set(self):
        self.provider.plan['clarification']='Which Iteration 3 item do you mean?'
        session,result = await self.ask('when is iteration 3 due')
        self.assertEqual([a['assignment_id'] for a in session.clarification['candidates']],[201,202,203])

    async def test_failed_canvas_read_can_retry_same_choice(self):
        import requests
        session = await self.clarify()
        self.fetch.side_effect = requests.ConnectionError('private')
        active = self.sessions.acquire(self.store.namespace,42,session.id)
        events = [e async for e in self.sessions.events(active,'all','retry',store=self.store,provider=self.provider)]
        self.assertEqual(events[-1]['event'],'error')
        self.assertTrue(session.clarification)
