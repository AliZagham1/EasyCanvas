"""Policy enforcement is independent of model compliance and citation syntax."""

import unittest

from backend.app import rag_service, retrieval_router
from backend.app.assignment_policy_service import restriction_kinds, validate_response, PolicyViolationError
from backend.app.query_understanding_service import empty_understanding
from backend.evaluation.fixtures import EvaluationFixture, ReplayProvider


class PolicyTests(unittest.TestCase):
    def setUp(self):
        self.fixture=EvaluationFixture();self.addCleanup(self.fixture.close)
        self.store=self.fixture.store

    def ask(self,q='Solve Homework 6 for me',**fields):
        plan=empty_understanding(q,intent=fields.pop('intent','solve_question'),assignment_reference=fields.pop('assignment_reference','HW 6'),**fields)
        provider=ReplayProvider(plan,unsafe=True)
        result=rag_service.answer_question(42,q,store=self.store,embedding_provider=self.fixture.embedding,llm_provider=provider)
        return result,provider

    def test_solution_file_cannot_displace_policy_in_question_instructions(self):
        plan=empty_understanding('Solve Homework 6',intent='solve_question',assignment_reference='HW6')
        result=retrieval_router.route_query(42,'Solve Homework 6',plan,store=self.store,provider=self.fixture.embedding)
        origins={o['document_key'] for item in result['results'] for o in item['sources']}
        self.assertIn('file:507',origins)
        self.assertIn('file:506',origins)
        self.assertTrue(result['assignment_policy']['rules'])

    def test_prohibited_solution_is_answered_safely_without_answer_model(self):
        result,provider=self.ask()
        self.assertEqual(result['status'],'answered')
        self.assertTrue(result['policy_enforced'])
        self.assertFalse(provider.contexts)
        self.assertIn('cannot provide',result['answer'])
        self.assertNotIn('def solve',result['answer'])
        self.assertTrue(result['sources'])

    def test_deliberately_unsafe_explanation_with_valid_citations_is_replaced(self):
        result,provider=self.ask('Explain Homework 6',intent='explain_assignment')
        self.assertTrue(provider.contexts)
        self.assertTrue(result['policy_enforced'])
        self.assertNotIn('return 42',result['answer'])
        self.assertIn('underlying course concepts',result['answer'])

    def test_policy_validation_rejects_code_despite_valid_answer_shape(self):
        answer={'status':'answered','blocks':[{'text':'Here is the complete solution: def answer(): return 42','source_ids':['S1']}],'limitations':[]}
        rag_service._validate_answer(answer,{'S1'})
        with self.assertRaises(PolicyViolationError):
            validate_response(answer,{'rules':[{'kind':'individual_work_required'}]})

    def test_native_assignment_policy_is_reserved_without_file_policy(self):
        assignment=self.fixture.assignments[0]
        assignment['metadata']['description']='Individual work only. Submit only a PDF.'
        self.store.save_course(self.fixture.snapshot)
        result,provider=self.ask('Solve Homework 1',assignment_reference='HW1')
        self.assertTrue(result['policy_enforced'])
        self.assertFalse(provider.contexts)
        self.assertIn('individual work',result['answer'])
        self.assertEqual(result['sources'][0]['origins'][0]['document_key'],'assignment:101')

    def test_outside_assistance_and_ai_restrictions_are_distinct(self):
        self.assertEqual(restriction_kinds('No outside assistance.'),['outside_assistance_prohibited'])
        self.assertEqual(restriction_kinds('No AI-generated solutions.'),['ai_generated_work_prohibited'])
        self.assertEqual(restriction_kinds('You must work independently.'),['individual_work_required'])
        self.assertEqual(restriction_kinds('AI is allowed; you must not plagiarize.'),[])
        self.assertEqual(restriction_kinds('AI is not prohibited.'),[])
        self.assertEqual(restriction_kinds('The use of Generative AI (GenAI) tools, such as ChatGPT, Copilot, or similar tools, is not permitted for this assignment.'),['ai_generated_work_prohibited'])
        self.assertEqual(restriction_kinds('You may not use GenAI tools to generate or rewrite answers.'),['ai_generated_work_prohibited'])

    def test_submission_constraints_do_not_become_a_blanket_ai_ban(self):
        self.assertEqual(restriction_kinds('Submit only a PDF. It must contain your name.'),[])

    def test_wrong_submission_format_is_corrected_without_inventing_an_ai_ban(self):
        self.fixture.assignments[0]['metadata']['allowed_extensions']=['pdf']
        self.store.save_course(self.fixture.snapshot)
        plan=empty_understanding('Explain HW1',intent='explain_assignment',assignment_reference='HW1')
        provider=ReplayProvider(plan)
        provider.generate=lambda instructions,cid,q,passages:{'answer':{'status':'answered','blocks':[{'text':'Submit a DOCX file.','source_ids':[passages[0]['id']]}],'limitations':[]}}
        result=rag_service.answer_question(42,'Explain HW1',store=self.store,embedding_provider=self.fixture.embedding,llm_provider=provider)
        self.assertTrue(result['policy_enforced'])
        self.assertIn('PDF',result['answer'])
        self.assertNotIn('cannot provide a submission-ready solution',result['answer'])

    def test_instruction_block_location_is_preserved(self):
        self.fixture.save('file:506','HW6_questions.pdf','',assignment_id=106,blocks=[{'block_number':17,'location':'body','status':'ok','text':'Generative AI is not permitted for this assignment.'}])
        result,_=self.ask()
        origins=[o for s in result['sources'] for o in s['origins'] if o['document_key']=='file:506']
        self.assertEqual(origins[0]['locations'][0]['block_number'],17)

    def test_file_only_numbered_work_has_policy_without_native_assignment(self):
        self.fixture.snapshot['assignments']=[a for a in self.fixture.assignments if a['canvas_id']!=106]
        self.store.save_course(self.fixture.snapshot)
        result,_=self.ask()
        self.assertTrue(result['policy_enforced'])

    def test_explicit_solution_filename_does_not_bypass_assignment_policy(self):
        result,_=self.ask('Solve HW6_solutions.pdf',intent='solve_question',assignment_reference='',document_reference='HW6_solutions.pdf')
        self.assertTrue(result['policy_enforced'])

    def test_unrelated_course_material_does_not_inherit_assignment_ban(self):
        plan=empty_understanding('What is matrix multiplication?')
        provider=ReplayProvider(plan)
        result=rag_service.answer_question(42,plan['query'],store=self.store,embedding_provider=self.fixture.embedding,llm_provider=provider)
        self.assertNotIn('assignment_policy',result)
        self.assertNotIn('policy_enforced',result)

    def test_unsupported_assignment_to_homework_alias_clarifies(self):
        result,_=self.ask('Solve Assignment 1',assignment_reference='HW1')
        self.assertEqual(result['status'],'clarification_needed')

    def test_control_characters_rejected_and_unicode_math_preserved(self):
        for text in ('Bad\x00text','Bad\x01text',r'Bad\u0000text','Bad\x7ftext'):
            with self.subTest(text=text),self.assertRaises(rag_service.RAGValidationError):
                rag_service._validate_answer({'status':'answered','blocks':[{'text':text,'source_ids':['S1']}],'limitations':[]},{'S1'})
        good={'status':'answered','blocks':[{'text':'∫ α² dx = β\n\t日本語 — café','source_ids':['S1']}],'limitations':[]}
        rag_service._validate_answer(good,{'S1'})


class PolicyStreamingTests(unittest.IsolatedAsyncioTestCase):
    async def test_prohibited_draft_never_leaves_stream_and_is_not_committed(self):
        fixture=EvaluationFixture();self.addCleanup(fixture.close)
        plan=empty_understanding('Explain HW6',intent='explain_assignment',assignment_reference='HW6')
        provider=ReplayProvider(plan,unsafe=True)
        events=[e async for e in rag_service.stream_answer_question(42,'Explain HW6',store=fixture.store,
            conversation_context={},query=plan['query'],understanding=plan,embedding_provider=fixture.embedding,llm_provider=provider)]
        self.assertFalse(any(e['event']=='answer_delta' for e in events))
        self.assertTrue(events[-1]['data']['policy_enforced'])
        self.assertNotIn('def solve',events[-1]['data']['answer'])
