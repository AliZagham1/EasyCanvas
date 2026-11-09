"""Test the evaluation harness itself; baseline behavior failures remain in reports."""

import subprocess
import sys
import unittest

from backend.evaluation.checks import answer_failures, routing_failures, understanding_failures
from backend.evaluation.fixtures import EvaluationFixture
from backend.evaluation.run import load_cases, production_fingerprint, render_report, run_synthetic, run_conversation_replays


class DatasetTests(unittest.TestCase):
    def test_cases_are_named_and_explicit(self):
        cases = load_cases()
        self.assertEqual(len(cases), 40)
        self.assertEqual(len({c['name'] for c in cases}), 40)
        self.assertTrue(all(c['data_origin']=='synthetic' and c['course_id']==42 for c in cases))
        self.assertTrue(all(c['expected'].get('intent') and c['expected'].get('retrieval_route') for c in cases))

    def test_required_paraphrase_categories_remain_present(self):
        cases = load_cases()
        categories = {c['category'] for c in cases}
        self.assertTrue({'assignment_aliasing','deadline_intent','number_safety','structured_rubric',
            'document_overview','focused_retrieval','conversation_context','policy_awareness'} <= categories)
        self.assertGreaterEqual(len([c for c in cases if c['category']=='assignment_aliasing']), 7)
        self.assertIn('walk me through assignment 1', [c['question'] for c in cases])

    def test_live_mode_requires_explicit_paid_flag(self):
        result = subprocess.run([sys.executable,'-m','backend.evaluation.run','--mode','live-query'],
                                text=True,capture_output=True,timeout=10)
        self.assertNotEqual(result.returncode,0)
        self.assertIn('require --allow-paid', result.stderr)


class CheckTests(unittest.TestCase):
    def setUp(self):
        self.fixture = EvaluationFixture()
        self.addCleanup(self.fixture.close)

    def test_reference_comparison_accepts_aliases_but_not_different_numbers(self):
        case = load_cases()[0]
        plan = self.fixture.plan(case)
        plan['assignment_reference']='homework #1'
        self.assertEqual(understanding_failures(case,plan,self.fixture),[])
        plan['assignment_reference']='HW10'
        self.assertTrue(any('assignment_reference' in f for f in understanding_failures(case,plan,self.fixture)))

    def test_routing_checks_actual_evidence_not_just_route_label(self):
        case = load_cases()[0]
        failures = routing_failures(case, {'retrieval_route':'assignment_material', 'retrieval_mode':'overview',
                                  'resolved_assignments':[101], 'results':[]})
        self.assertTrue(any('missing required evidence' in failure for failure in failures))

    def answer(self, text, status='answered'):
        return {'status':status,'answer':text,'answer_blocks':[{'text':text,'source_ids':['S1']}],
                'retrieved_sources':[{'id':'S1'}]}

    def test_policy_checker_rejects_solution_and_false_missing_context(self):
        failures = answer_failures(self.answer('Here is the complete submission-ready solution: def solve(): return 42'),'policy')
        self.assertTrue(any('submission-ready' in f for f in failures))
        self.assertTrue(answer_failures(self.answer('No material found','insufficient_context'),'policy'))

    def test_policy_refusal_can_offer_concept_help_without_false_positive(self):
        text = ('The assignment does not permit generative AI. I cannot provide a submission-ready solution. '
                'I can explain the underlying concepts and clarify the task.')
        self.assertEqual(answer_failures(self.answer(text),'policy'),[])

    def test_formatting_checks_control_characters(self):
        for text in ('Matrix \x00A', r'Matrix \u0000A'):
            self.assertTrue(answer_failures(self.answer(text),'focused'))

    def test_answer_wording_is_not_compared_exactly(self):
        for text in ('Content: 24 points. Slides: 24. Presentation style: 24. Demo: 28.',
                     '**Content** earns up to **24**, **Slides** up to **24**, Presentation style up to 24, Demo up to 28.'):
            self.assertEqual(answer_failures(self.answer(text),'rubric'),[])

    def test_report_does_not_hide_failure_or_blocked_evidence(self):
        rows = [{'layer':'query_understanding','status':status,'name':status,'question':'A question',
                 'expected':{'intent':'explain_assignment'}, 'actual':{'intent':'get_deadline'},
                 'reasons':['wrong intent'], 'evidence_kind':'live model', 'likely_failing_layer':'query_understanding'}
                for status in ('fail','blocked')]
        report = render_report({'timestamp':'fixed','mode':'live-query','results':rows})
        self.assertIn('fail (fail)',report)
        self.assertIn('blocked (blocked)',report)
        self.assertIn('wrong intent',report)


class HarnessExecutionTests(unittest.IsolatedAsyncioTestCase):
    async def test_declared_plan_runs_do_not_become_claimed_live_model_evidence(self):
        before = production_fingerprint()
        rows = await run_synthetic(load_cases()[:1])
        self.assertEqual([r['status'] for r in rows],['pass','pass'])
        self.assertEqual(rows[0]['layer'],'query_contract')
        self.assertIn('NOT a natural-language',rows[0]['evidence_kind'])
        self.assertEqual(before,production_fingerprint())

    async def test_conversation_replay_reports_context_and_selected_passages(self):
        rows = await run_conversation_replays(load_cases())
        self.assertEqual(len(rows),6)
        self.assertTrue(all(r['status']=='pass' for r in rows))
        self.assertTrue(rows[0]['actual']['passages_to_fake_answer_provider'])
        self.assertEqual(rows[4]['actual']['active_assignments'],[])
