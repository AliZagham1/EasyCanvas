"""General concept answers must stay separate from Canvas and cited RAG answers."""

import unittest
from unittest.mock import patch

from backend.app.query_understanding_service import empty_understanding
from backend.app.rag_service import RAGValidationError, answer_question, stream_answer_question
from backend.app.llm_service import answer_schema
from backend.app.conversation_service import ConversationService
from backend.app.conversation_store import ConversationStore
from backend.evaluation.fixtures import ReplayProvider
from backend.tests.test_query_routing import RoutingFixture


class GeneralProvider:
    def __init__(self, plan, answer=None):
        self.plan = plan
        self.calls = []
        self.answer = answer or {'status': 'answered', 'blocks': [
            {'text': 'Recursion means a function solves a smaller version of the same problem.', 'source_ids': []}],
            'limitations': []}

    async def rewrite_query(self, course_id, question, context):
        return self.plan

    def generate_general(self, instructions, question):
        self.calls.append(('general', question))
        return {'answer': self.answer, 'model': 'fake', 'usage': None}

    async def stream_general(self, instructions, question, context=None):
        self.calls.append(('stream_general', question, context))
        yield {'event': 'answer_delta', 'data': {'blocks': [self.answer['blocks'][0]['text']]}}
        yield {'event': 'complete', 'data': {'answer': self.answer, 'model': 'fake', 'usage': None}}

    def generate(self, instructions, course_id, question, passages):
        self.calls.append(('grounded', question))
        return {'answer': {'status': 'insufficient_context', 'blocks': [], 'limitations': []},
                'model': 'fake', 'usage': None}


class ConversationProvider(ReplayProvider):
    """Use existing fake grounded answers plus an uncited concept answer."""

    def __init__(self, plan):
        super().__init__(plan)
        self.general_contexts = []

    async def stream_general(self, instructions, question, context=None):
        self.general_contexts.append(context)
        answer = {'status': 'answered', 'blocks': [{'text': 'RAG retrieves relevant passages before answering.',
                                                   'source_ids': []}], 'limitations': []}
        yield {'event': 'answer_delta', 'data': {'blocks': [answer['blocks'][0]['text']]}}
        yield {'event': 'complete', 'data': {'answer': answer, 'model': 'fake', 'usage': None}}


class GeneralConceptTests(RoutingFixture, unittest.TestCase):
    def test_general_answer_schema_has_no_empty_enum(self):
        item = answer_schema([])['properties']['blocks']['items']['properties']['source_ids']['items']
        self.assertEqual(item, {'type': 'string'})

    def test_self_contained_concept_uses_no_embedding_or_course_sources(self):
        question = 'What is recursion?'
        provider = GeneralProvider(empty_understanding(question, intent='general_concept'))
        result = answer_question(42, question, store=self.store, embedding_provider=self.embedding, llm_provider=provider)
        self.assertEqual(result['status'], 'answered')
        self.assertEqual(result['answer_basis'], 'general_knowledge')
        self.assertEqual(result['retrieval_route'], 'general_concept')
        self.assertEqual(result['sources'], [])
        self.assertEqual(result['answer_blocks'][0]['source_ids'], [])
        self.assertEqual(provider.calls, [('general', question)])
        self.assertEqual(self.embedding.calls, [])

    def test_model_added_course_reference_is_ignored_in_different_courses(self):
        other = {**self.snapshot, 'course_id': 43,
                 'course': {'metadata': {'course_code': 'CSE 3320', 'name': 'Operating Systems'}}}
        self.store.save_course(other)
        for course_id, code in ((42, 'CSE 3311'), (43, 'CSE 3320')):
            with self.subTest(course_id=course_id):
                question = 'What is RAG?'
                plan = empty_understanding(question, intent='course_wide_question',
                                           course_reference=code, assignment_reference='Lab 4')
                provider = GeneralProvider(plan)
                result = answer_question(course_id, question, store=self.store,
                                         embedding_provider=self.embedding, llm_provider=provider)
                self.assertEqual(result['retrieval_route'], 'general_concept')
                self.assertEqual(result['answer_basis'], 'general_knowledge')
                self.assertEqual(result['query_understanding']['course_reference'], '')
                self.assertEqual(result['query_understanding']['assignment_reference'], '')
                self.assertEqual(provider.calls, [('general', question)])
        self.assertEqual(self.embedding.calls, [])

    def test_standalone_examples_and_explicit_context_boundaries(self):
        for question in ('What is RAG?', 'What is recursion?', 'Explain branch coverage',
                         'What is a Boolean predicate?'):
            with self.subTest(question=question):
                result = self.route(question, empty_understanding(question, intent='course_wide_question',
                                          course_reference='CSE 3311'))
                self.assertEqual(result['retrieval_route'], 'general_concept')
        for question in ('What is RAG in this class?', 'Explain recursion in Homework 6',
                         'What does this syllabus say about attendance?', 'What is the second requirement?',
                         'How many points is that worth?'):
            with self.subTest(question=question):
                result = self.route(question, empty_understanding(question, intent='course_wide_question',
                                          course_reference='CSE 3311'))
                self.assertNotEqual(result['retrieval_route'], 'general_concept')

    def test_course_or_current_fact_question_cannot_escape_to_general(self):
        for question in ('What is the attendance policy for this class?',
                         'When is my homework due?', 'Explain recursion in my homework',
                         'What is in this PDF?'):
            with self.subTest(question=question):
                plan = empty_understanding(question, intent='general_concept')
                result = self.route(question, plan)
                self.assertNotEqual(result['retrieval_route'], 'general_concept')

    def test_active_followup_cannot_escape_to_general(self):
        question = 'Explain that more simply'
        plan = empty_understanding(question, intent='general_concept', use_active_documents=True,
                                   operation='continue')
        result = self.route(question, plan, context={'active_documents': [{'document_key': 'file:501'}],
                                                      'recent_turns': [{'question': 'Explain homework 1'}]})
        self.assertNotEqual(result['retrieval_route'], 'general_concept')

    def test_general_followup_retains_only_general_context(self):
        question = 'Explain that more simply'
        plan = empty_understanding(question, intent='general_concept', use_active_documents=True,
                                   operation='continue')
        context = {'conversation_state': {'current_intent': 'general_concept'},
                   'recent_turns': [{'question': 'What is recursion?', 'answer': 'A function calls itself.'}],
                   'active_documents': [], 'active_assignments': []}
        result = self.route(question, plan, context=context)
        self.assertEqual(result['retrieval_route'], 'general_concept')

    def test_general_answer_rejects_invented_course_citations(self):
        question = 'What is recursion?'
        answer = {'status': 'answered', 'blocks': [{'text': 'It is in your course [S1].', 'source_ids': ['S1']}],
                  'limitations': []}
        provider = GeneralProvider(empty_understanding(question, intent='general_concept'), answer)
        with self.assertRaises(RAGValidationError):
            answer_question(42, question, store=self.store, llm_provider=provider)


class GeneralConceptStreamTests(RoutingFixture, unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.service = ConversationService(repository=ConversationStore(self.store.path))
        self.session = None

    async def turn(self, question, plan):
        provider = ConversationProvider(plan)
        session = self.service.acquire(self.store.namespace, 42, self.session.id if self.session else None)
        self.session = session
        events = [event async for event in self.service.events(
            session, question, str(len(session.turns) + 1), store=self.store,
            provider=provider, embedding_provider=self.embedding)]
        self.assertEqual(events[-1]['event'], 'final', events[-1])
        return events[-1]['data'], provider

    async def test_streaming_general_answer_uses_same_route(self):
        question = 'What is recursion?'
        provider = GeneralProvider(empty_understanding(question, intent='general_concept'))
        events = [event async for event in stream_answer_question(
            42, question, store=self.store, conversation_context={}, query=question,
            embedding_provider=self.embedding, llm_provider=provider)]
        self.assertEqual([event['event'] for event in events], ['generating', 'answer_delta', 'final'])
        self.assertEqual(events[-1]['data']['answer_basis'], 'general_knowledge')
        self.assertEqual(self.embedding.calls, [])

    async def test_rag_then_standalone_concept_clears_old_document_context(self):
        first, _ = await self.turn('Explain HW1', empty_understanding('Explain HW1', intent='explain_assignment',
                                       assignment_reference='HW1'))
        self.assertEqual(first['retrieval_route'], 'assignment_material')
        self.assertTrue(self.session.active_documents)
        question = 'What is RAG?'
        second, provider = await self.turn(question, empty_understanding(question, intent='course_wide_question',
                                                  course_reference='CSE 3311', assignment_reference='HW1'))
        self.assertEqual(second['retrieval_route'], 'general_concept')
        self.assertEqual(provider.general_contexts, [{}])
        self.assertEqual(self.session.active_documents, [])
        self.assertEqual(self.session.active_assignments, [])

    async def test_structured_canvas_then_standalone_concept(self):
        with patch('backend.app.structured_canvas_service.current_assignment',
                   return_value=self.assignments[0]):
            first, _ = await self.turn('When is HW1 due?', empty_understanding('When is HW1 due?',
                                        intent='get_deadline', assignment_reference='HW1'))
        self.assertEqual(first['retrieval_route'], 'current_assignment_facts')
        second, _ = await self.turn('What is RAG?', empty_understanding('What is RAG?',
                                   intent='course_wide_question', course_reference='CSE 3311'))
        self.assertEqual(second['retrieval_route'], 'general_concept')
        self.assertEqual(self.session.state['current_intent'], 'general_concept')

    async def test_general_concept_then_course_specific_rag(self):
        first, _ = await self.turn('What is RAG?', empty_understanding('What is RAG?',
                                   intent='course_wide_question', course_reference='CSE 3311'))
        self.assertEqual(first['retrieval_route'], 'general_concept')
        second, provider = await self.turn('Explain Homework 1', empty_understanding('Explain Homework 1',
                                       intent='explain_assignment', assignment_reference='HW1'))
        self.assertEqual(second['retrieval_route'], 'assignment_material')
        self.assertTrue(second['sources'])
        self.assertFalse(provider.general_contexts)

    async def test_shorthand_followup_keeps_assignment_and_part(self):
        await self.turn('Explain HW1', empty_understanding('Explain HW1', intent='explain_assignment',
                                assignment_reference='HW1'))
        result, _ = await self.turn('What about part b?', empty_understanding('What about part b?',
                                    intent='explain_question', part='b', use_active_documents=True,
                                    operation='continue'))
        self.assertEqual(result['retrieval_route'], 'assignment_material')
        self.assertEqual(result['resolved_assignments'], [101])
        self.assertEqual(self.session.state['active_part'], 'b')

    async def test_explicit_course_wording_stays_grounded(self):
        question = 'What is RAG in this class?'
        result, provider = await self.turn(question, empty_understanding(question, intent='course_wide_question',
                                                    course_reference='CSE 3311'))
        self.assertNotEqual(result['retrieval_route'], 'general_concept')
        self.assertFalse(provider.general_contexts)
