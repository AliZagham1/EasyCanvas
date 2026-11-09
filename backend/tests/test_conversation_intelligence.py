"""Conversation operations, real temporary SQLite history and course isolation."""

import asyncio
import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.app import rag_service, retrieval_router
from backend.app.conversation_service import ConversationService, ConversationError
from backend.app.conversation_store import ConversationStore
from backend.app.conversation_state_service import interpret_turn, answer_context
from backend.app.query_understanding_service import empty_understanding, validate_understanding
from backend.evaluation.fixtures import EvaluationFixture, ReplayProvider, NOW


class ConversationIntelligenceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.fixture = EvaluationFixture()
        self.addCleanup(self.fixture.close)
        self.store = self.fixture.store
        self.embedding = self.fixture.embedding
        self.repo = ConversationStore(self.store.path)
        self.service = ConversationService(repository=self.repo)
        self.session = None
        self.records = self.fixture.snapshot['assignments']
        for aid, title in [(301, 'Final Deliverables (Presentation)'), (302, 'Final Deliverables (Written Deliverable)'),
                           (401, 'Homework: Static Analysis'), (402, 'Homework: Specification')]:
            self.records.append({'canvas_id':aid, 'course_id':42, 'title':title, 'source_type':'assignment',
                'metadata':{'id':aid, 'course_id':42, 'name':title, 'due_at':'2026-12-12T05:59:00Z'}})
            self.fixture.save(f'assignment:{aid}',title,f'Read and explain the {title} requirements. Keep all required parts.')
        self.store.save_course(self.fixture.snapshot)
        from backend.app.chunking_service import ensure_course_chunks
        from backend.app.embedding_service import embed_course
        ensure_course_chunks(42,self.store)
        embed_course(42,store=self.store,provider=self.embedding)
        self.current = patch('backend.app.structured_canvas_service.current_assignments',side_effect=lambda cid: copy.deepcopy(self.records)).start()
        self.single = patch('backend.app.structured_canvas_service.current_assignment',side_effect=lambda cid,r: copy.deepcopy(r)).start()
        self.addCleanup(patch.stopall)
        self.provider = ReplayProvider(empty_understanding('initial'))

    async def ask(self, question, **plan):
        self.provider.plan = empty_understanding(question, **plan)
        session = self.service.acquire(self.store.namespace,42,self.session.id if self.session else None)
        self.session = session
        events = [e async for e in self.service.events(session,question,str(len(session.turns)+1),store=self.store,
                   provider=self.provider,embedding_provider=self.embedding)]
        self.assertEqual(events[-1]['event'],'final',events[-1])
        return events[-1]['data']

    async def test_deadline_intent_carries_to_new_final_entity(self):
        await self.ask('when is final presentation due',intent='get_deadline',assignment_reference='Final Presentation')
        result = await self.ask('what about final written deliverable',intent='explain_assignment',assignment_reference='Final Written Deliverable')
        self.assertEqual(result['resolved_assignments'],[302])
        self.assertEqual(result['retrieval_route'],'current_assignment_facts')
        self.assertIn('deadline',result['answer'])

    async def test_short_written_one_retains_deadline_intent(self):
        await self.ask('when is final presentation due',intent='get_deadline',assignment_reference='Final Presentation')
        result = await self.ask('and the written one',intent='explain_assignment',assignment_reference='written one')
        self.assertEqual(result['resolved_assignments'],[302])
        self.assertEqual(result['query_understanding']['intent'],'get_deadline')

    async def test_correction_selects_presentation_in_current_final_topic(self):
        await self.ask('when is final written deliverable due',intent='get_deadline',assignment_reference='Final Written Deliverable')
        result = await self.ask('no the presentation',intent='explain_assignment')
        self.assertEqual(result['resolved_assignments'],[301])
        self.assertEqual(result['query_understanding']['intent'],'get_deadline')

    async def test_homework_list_is_structured_and_exclusions_are_not_positive_references(self):
        first = await self.ask('what homeworks we have in this course')
        self.assertEqual(first['retrieval_route'],'current_assignment_list')
        self.assertIn(401,first['resolved_assignments'])
        self.assertNotIn(301,first['resolved_assignments'])
        result = await self.ask('any homework other than static-analysis',assignment_reference='Homework: Static Analysis')
        self.assertNotIn(401,result['resolved_assignments'])
        self.assertIn(402,result['resolved_assignments'])
        self.assertEqual(result['query_understanding']['excluded_references'],['static-analysis'])
        self.assertFalse(self.provider.contexts)  # No answer model or RAG used for lists/facts.

    async def test_exclusion_paraphrases(self):
        for phrase in ('anything besides Static Analysis','all except Static Analysis','any homework other than Static Analysis'):
            with self.subTest(phrase=phrase):
                self.session = None
                await self.ask('what homeworks do we have')
                result = await self.ask(phrase)
                self.assertNotIn(401,result['resolved_assignments'])
                self.assertIn(402,result['resolved_assignments'])

    async def test_implicit_other_excludes_active_entity(self):
        for phrase in ('anything else','what else do we have','another one','not that one','anything besides that','any other homework'):
            with self.subTest(phrase=phrase):
                self.session = None
                await self.ask('Tell me about Homework: Static Analysis',intent='explain_assignment',assignment_reference='Homework: Static Analysis')
                result = await self.ask(phrase)
                self.assertNotIn(401,result['resolved_assignments'])
                self.assertIn(402,result['resolved_assignments'])

    async def test_explain_other_uses_prior_list_and_current_selection(self):
        self.records[:] = [r for r in self.records if r['canvas_id'] in (401,402)]
        self.store.save_course(self.fixture.snapshot)
        await self.ask('what homeworks do we have')
        await self.ask('Explain Static Analysis',intent='explain_assignment',assignment_reference='Homework: Static Analysis')
        result = await self.ask('explain the other one',intent='explain_assignment',use_active_documents=True)
        self.assertEqual(result['resolved_assignments'],[402])

    async def test_no_context_other_one_clarifies(self):
        result = await self.ask('tell me about the other one',intent='explain_assignment')
        self.assertEqual(result['status'],'clarification_needed')
        self.assertFalse(self.provider.contexts)

    async def test_asking_what_homework_requires_is_not_an_assignment_list(self):
        result = await self.ask('what is being asked in homework 6',intent='explain_assignment',assignment_reference='HW 6')
        self.assertEqual(result['retrieval_route'],'assignment_material')
        self.assertIn('file:506',result['resolved_documents'])

    async def test_next_deadline_uses_nearest_future_structured_assignment(self):
        plan = empty_understanding('What is due next?',intent='get_deadline')
        result = retrieval_router.route_query(42,'What is due next?',plan,store=self.store,now=NOW)
        self.assertEqual(result['resolved_assignments'],[106])
        self.assertTrue(result['direct_response'])

    async def test_group_listing_filters_actual_canvas_entities(self):
        result = await self.ask('Which Iteration 3 items exist?',intent='list_assignments',assignment_reference='Iteration 3')
        self.assertEqual(result['resolved_assignments'],[201])
        self.assertEqual(result['retrieval_route'],'current_assignment_list')

    async def test_rubric_shorthand_keeps_current_rubric(self):
        await self.ask('How is Iteration 3 Presentation graded?',intent='explain_rubric',assignment_reference='Iteration 3 Presentation')
        result = await self.ask('how much is demo',intent='get_assignment_details',assignment_reference='demo')
        self.assertEqual(result['retrieval_route'],'saved_assignment_rubric')
        self.assertIn('Demo', '\n'.join(p['text'] for p in self.provider.contexts[-1]))
        self.assertEqual(result['resolved_assignments'],[201])

    async def test_part_simplification_and_topic_switch(self):
        await self.ask('Explain Homework 1',intent='explain_assignment',assignment_reference='HW1')
        await self.ask('what about part b',intent='explain_question',part='b',use_active_documents=True)
        await self.ask('explain that simpler',intent='explain_question')
        self.assertEqual(self.session.state['active_part'],'b')
        self.assertTrue(self.provider.contexts[-1])
        result = await self.ask('How is Iteration 3 Presentation graded?',intent='explain_rubric',assignment_reference='Iteration 3 Presentation')
        self.assertEqual(result['resolved_assignments'],[201])
        self.assertEqual(self.session.state['active_part'],'')

    async def test_clarified_question_part_retrieves_ordered_section_and_policy(self):
        from backend.app.chunking_service import ensure_course_chunks
        from backend.app.embedding_service import embed_course
        from backend.evaluation.fixtures import POLICY
        pages = [
            {'page_number': 1, 'status': 'ok', 'text': POLICY},
            {'page_number': 2, 'status': 'ok', 'text': 'Question 2. Ordering language\n1. Every command starts with a keyword.'},
            {'page_number': 3, 'status': 'ok', 'text': '2. Include an item.\nb. Rewrite the grammar in EBNF.'},
            {'page_number': 4, 'status': 'ok', 'text': 'Use braces for repetition and brackets for optional elements.'},
            {'page_number': 5, 'status': 'ok', 'text': 'Question 3. A separate exercise.'},
            {'page_number': 6, 'status': 'ok', 'text': 'Question 2. Ordering language\nQuestion 3. Separate exercise'},
        ]
        self.fixture.save('file:506', 'HW6_questions.pdf', '', assignment_id=106, pages=pages)
        ensure_course_chunks(42, self.store)
        embed_course(42, store=self.store, provider=self.embedding)
        await self.ask('Explain HW6', intent='explain_assignment', assignment_reference='HW6')
        clarification = await self.ask('Explain part b', intent='explain_question', assignment_reference='HW6',
                                       part='b', use_active_documents=True, operation='continue',
                                       clarification='Which question number?')
        self.assertEqual(clarification['status'], 'clarification_needed')
        self.assertEqual(self.session.state['active_part'], 'b')
        result = await self.ask('Question 2', intent='explain_question', assignment_reference='HW6',
                                part='b', question_number='2', use_active_documents=True, operation='continue',
                                query='Explain question 2 part b of HW6')
        self.assertEqual(result['retrieval_route'], 'assignment_material')
        self.assertEqual(result['query_understanding']['part'], 'b')
        self.assertEqual(result['query_understanding']['question_number'], '2')
        passages = self.provider.contexts[-1]
        self.assertTrue(any('Rewrite the grammar in EBNF' in passage['text'] for passage in passages))
        self.assertTrue(result['assignment_policy']['rules'])
        self.assertTrue(any('not permitted' in passage['text'] for passage in passages))

    async def test_restart_restores_messages_state_and_retry_cache(self):
        result = await self.ask('Explain Homework 1',intent='explain_assignment',assignment_reference='HW1')
        sid = self.session.id
        restored = ConversationService(repository=self.repo).acquire(self.store.namespace,42,sid)
        self.assertEqual(restored.active_assignments,[101])
        self.assertEqual(restored.state['current_intent'],'explain_assignment')
        self.assertEqual(restored.turns['1'][1],result)
        saved = self.repo.load(self.store.namespace,42,sid)
        self.assertEqual([m['role'] for m in saved['messages']],['user','assistant'])
        self.assertEqual(saved['messages'][1]['response']['sources'],result['sources'])

    async def test_new_thread_is_independent_and_old_thread_recoverable(self):
        await self.ask('Explain Homework 1',intent='explain_assignment',assignment_reference='HW1')
        old = self.session.id
        new = self.service.acquire(self.store.namespace,42);new.busy = False
        self.assertNotEqual(old,new.id)
        self.assertFalse(new.history);self.assertFalse(new.state)
        self.assertEqual(len(self.repo.list(self.store.namespace,42)),2)
        self.assertTrue(self.repo.load(self.store.namespace,42,old)['messages'])

    async def test_persisted_account_and_course_isolation(self):
        await self.ask('Explain Homework 1',intent='explain_assignment',assignment_reference='HW1')
        for namespace,cid in [('other',42),(self.store.namespace,43)]:
            self.assertIsNone(self.repo.load(namespace,cid,self.session.id))
            with self.assertRaises(ConversationError):
                ConversationService(repository=self.repo).acquire(namespace,cid,self.session.id)

    async def test_context_stays_bounded_and_old_messages_remain_stored(self):
        for i in range(6):
            await self.ask('Explain Homework 1 '+str(i),intent='explain_assignment',assignment_reference='HW1')
        context = self.service.context(self.session,self.store)
        self.assertLessEqual(len(context['recent_turns']),3)
        self.assertLessEqual(sum(len(t[k]) for t in context['recent_turns'] for k in ('question','answer')),6000)
        self.assertEqual(len(self.repo.load(self.store.namespace,42,self.session.id)['messages']),12)
        self.assertFalse(answer_context(context,empty_understanding('new topic'))['recent_turns'])

    async def test_failed_turn_keeps_question_but_not_unvalidated_answer(self):
        session = self.service.acquire(self.store.namespace,42)
        self.repo.begin_turn(session,'one','Question')
        saved = self.repo.load(self.store.namespace,42,session.id)
        self.assertEqual(saved['messages'][0]['role'],'user')
        self.assertIsNone(saved['messages'][0]['response'])


class AnswerDepthTests(unittest.TestCase):
    def test_explanation_depth_and_simpler_are_not_automatically_short(self):
        for q in ('Explain Lab 4','Explain Homework 1','Explain Lab 4 like I am a beginner','I do not understand, explain simpler'):
            instructions = rag_service.answer_instructions(q,empty_understanding(q,intent='explain_assignment'))
            self.assertIn('reasonably detailed',instructions)
            self.assertIn('Never invent detail',instructions)
        self.assertIn('Keep this response concise',rag_service.answer_instructions('give me the short version'))

    def test_invalid_constraint_shapes_are_rejected(self):
        for changes in ({'excluded_references':[1]}, {'operation':'invent'}, {'entity_type':'made-up'}):
            with self.assertRaises(ValueError):validate_understanding(empty_understanding('Question',**changes))
