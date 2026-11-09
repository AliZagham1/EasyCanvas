"""Explicit evaluation CLI. Offline is free; live calls require --allow-paid.

Reports preserve failures. This module never patches production source or indexes
the saved course database. Deterministic replay results are not model-quality scores.
"""

import argparse
import asyncio
import contextlib
import copy
from contextlib import aclosing
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import unittest
from unittest.mock import patch

from backend.app import rag_service, retrieval_router
from backend.app.conversation_service import ConversationService
from backend.app.ingestion_store import IngestionStore, DEFAULT_DATABASE
from backend.app.query_understanding_service import understand_question
from backend.evaluation.checks import understanding_failures, routing_failures, answer_failures, source_keys
from backend.evaluation.fixtures import EvaluationFixture, ReplayProvider, NOW

ROOT = Path(__file__).resolve().parents[2]
DATASET = Path(__file__).with_name('prompts.json')


def load_cases():
    data = json.loads(DATASET.read_text())
    cases = data['cases']
    if len({c['name'] for c in cases}) != len(cases):
        raise ValueError('Dataset case names must be unique.')
    return cases


def production_fingerprint():
    paths = [p for directory in ('backend/app', 'frontend/app', 'frontend/lib')
             for p in (ROOT / directory).rglob('*') if p.is_file() and '__pycache__' not in p.parts]
    return {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}


def record(case, layer, actual, failures=(), *, status=None, evidence='deterministic', likely_layer=None):
    return {'name': case['name'], 'category': case['category'], 'layer': layer,
            'question': case.get('question', ''), 'expected': case.get('expected', {}),
            'conversation_context': case.get('conversation_context'),
            'actual': actual, 'status': status or ('fail' if failures else 'pass'),
            'reasons': list(failures), 'likely_failing_layer': likely_layer or layer,
            'evidence_kind': evidence,
            'recent_work_coverage': case.get('recent_work_coverage', 'separate extraction/quality layer')}


def route_summary(result):
    return {key: result.get(key) for key in ('status', 'retrieval_route', 'query_understanding', 'resolved_assignments',
        'resolved_documents', 'retrieval_mode', 'evidence_coverage', 'notice')} | {
        'source_keys': source_keys(result), 'passages': result.get('results', [])}


@contextlib.contextmanager
def fixture_clock_and_canvas(fixture):
    """Freeze time and external structured reads only; run real routing/retrieval."""
    with patch('backend.app.retrieval_router.datetime', wraps=datetime) as clock, \
         patch('backend.app.structured_canvas_service.canvas_service.get_assignment',
               side_effect=lambda cid, aid: next(copy.deepcopy(a['metadata']) for a in fixture.assignments if a['canvas_id'] == aid)), \
         patch('backend.app.structured_canvas_service.canvas_service.get_assignments',
               side_effect=lambda cid: [copy.deepcopy(a['metadata']) for a in fixture.assignments]):
        clock.now.return_value = NOW
        yield


async def run_synthetic(cases, *, live=False):
    fixture, rows = EvaluationFixture(), []
    if live:
        from backend.app.llm_service import OpenAIAnswerProvider
        provider = OpenAIAnswerProvider()
    try:
        with fixture_clock_and_canvas(fixture):
            for case in cases:
                plan = fixture.plan(case)
                if live:
                    try:
                        plan = await understand_question(42, case['question'], store=fixture.store, provider=provider,
                                                        conversation_context=fixture.context(case['conversation_context']))
                        failures = understanding_failures(case, plan, fixture)
                        rows.append(record(case, 'query_understanding', plan, failures, evidence='live model / synthetic course'))
                    except Exception as exc:
                        rows.append(record(case, 'query_understanding', {'error_type': type(exc).__name__},
                            ['Provider/setup failure; understanding was not evaluated.'], status='blocked', evidence='live model'))
                        continue
                else:
                    rows.append(record(case, 'query_contract', plan, understanding_failures(case, plan, fixture),
                                       evidence='declared fixture; NOT a natural-language evaluation'))
                try:
                    result = retrieval_router.route_query(42, case['question'], plan, store=fixture.store,
                        provider=fixture.embedding, conversation_context=fixture.context(case['conversation_context']), now=NOW)
                    failures = routing_failures(case, result)
                    rows.append(record(case, 'retrieval_routing', route_summary(result), failures,
                        likely_layer='query_understanding' if live and rows[-1]['status'] == 'fail' else 'retrieval/entity resolution',
                        evidence='live plan + real local retrieval' if live else 'declared plan + real local retrieval'))
                except Exception as exc:
                    rows.append(record(case, 'retrieval_routing', {'error_type': type(exc).__name__},
                                       ['Routing could not complete.'], likely_layer='retrieval/entity resolution'))
    finally:
        fixture.close()
    return rows


async def run_conversation_replays(cases):
    """Exercise real in-memory context and real retrieval with explicit model replays."""
    fixture, sessions, rows = EvaluationFixture(), ConversationService(), []
    sequence = ('assignment_alias_1', 'followup_b', 'followup_function', 'followup_solution', 'topic_change')
    by_name = {c['name']: c for c in cases}
    sid = None
    try:
        with fixture_clock_and_canvas(fixture):
            for index, name in enumerate(sequence):
                case = by_name[name]
                session = sessions.acquire(fixture.store.namespace, 42, sid)
                provider = ReplayProvider(fixture.plan(case))
                events = [e async for e in sessions.events(session, case['question'], str(index),
                    store=fixture.store, provider=provider, embedding_provider=fixture.embedding)]
                sid = session.id
                final = events[-1]
                result = final['data']
                failures = []
                if final['event'] != 'final' or result.get('status') != 'answered':
                    failures.append('Conversation did not return a validated answer.')
                if provider.rewrite_calls != 1:
                    failures.append('Turn did not use exactly one understanding call.')
                expected = case['expected']['resolved_assignment_ids']
                if expected and session.active_assignments != expected:
                    failures.append('Active assignment context was not retained.')
                if name == 'topic_change' and (session.active_assignments or 'assignment:101' in session.active_documents):
                    failures.append('Old homework focus leaked into the new topic.')
                rows.append(record(case, 'conversation_context', {'response': result, 'source_keys': source_keys(result),
                    'active_documents': session.active_documents, 'active_assignments': session.active_assignments,
                    'passages_to_fake_answer_provider': provider.contexts, 'events': [e['event'] for e in events]}, failures,
                    evidence='real conversation/retrieval; declared plans and answers'))
            case = {'name': 'course_account_switch', 'category': 'isolation', 'question': 'Reuse another course/account conversation',
                    'expected': {'behavior': 'reject cross-course and cross-account session'}}
            failures = []
            for namespace, cid in ((fixture.store.namespace, 43), ('different-account', 42)):
                try:
                    sessions.acquire(namespace, cid, sid)
                    failures.append('Cross-course/account session was accepted.')
                except ValueError:
                    pass
            rows.append(record(case, 'conversation_context', {'course_and_account_checked': True}, failures))
    finally:
        fixture.close()
    return rows


def run_answer_guards(cases):
    """Probe actual validation with unsafe output; expose missing enforcement honestly."""
    fixture, rows = EvaluationFixture(), []
    try:
        case = next(c for c in cases if c['name'] == 'policy_solve')
        provider = ReplayProvider(fixture.plan(case), unsafe=True)
        with fixture_clock_and_canvas(fixture):
            # The synchronous service has no conversation argument, so provide the explicit
            # resolved title in the declared plan; the student wording stays unchanged.
            provider.plan['assignment_reference'] = 'HW 6'
            result = rag_service.answer_question(42, case['question'], store=fixture.store,
                    embedding_provider=fixture.embedding, llm_provider=provider)
        failures = answer_failures(result, 'policy')
        rows.append(record({**case, 'name': 'unsafe_policy_answer_replay'}, 'answer_guard',
            {'response': result, 'passages_to_fake_answer_provider': provider.contexts}, failures,
            evidence='adversarial provider replay; NOT proof a live model always violates policy', likely_layer='answer generation/validation'))
        for name, body, should_accept in (
            ('unknown_citation', {'status':'answered','blocks':[{'text':'Unsupported','source_ids':['S99']}],'limitations':[]}, False),
            ('valid_citation', {'status':'answered','blocks':[{'text':'Supported','source_ids':['S1']}],'limitations':[]}, True),
            ('malformed_math_control', {'status':'answered','blocks':[{'text':'Projection \x00UU^Ty','source_ids':['S1']}],'limitations':[]}, False),
        ):
            accepted = True
            try:
                rag_service._validate_answer(body, {'S1'})
            except rag_service.RAGValidationError:
                accepted = False
            c = {'name':name,'category':'answer_validation','question':'Controlled provider output',
                 'expected':{'accepted':should_accept}}
            rows.append(record(c, 'answer_guard', {'accepted':accepted,'provider_output':body},
                [] if accepted == should_accept else ['Response validator accepted output that violates this evaluation invariant.'],
                likely_layer='response validation'))
    finally:
        fixture.close()
    return rows


COMPONENT_TESTS = {
    'extraction': [
        'test_visual_documents.InspectionTests.test_native_only_and_blank_pages_do_not_need_vision',
        'test_visual_documents.InspectionTests.test_scanned_page_is_detected',
        'test_visual_documents.InspectionTests.test_substantial_native_text_does_not_hide_an_image',
        'test_visual_documents.InspectionTests.test_vectors_without_images_are_detected',
        'test_visual_documents.InspectionTests.test_repeated_tiny_marginal_logo_is_skipped_only_on_text_rich_pages',
        'test_visual_documents.EnrichmentTests.test_storage_chunking_and_embeddings_preserve_identity_and_reuse',
        'test_visual_documents.EnrichmentTests.test_unreadable_details_remain_partial',
    ],
    'warning_and_coverage': [
        'test_document_overviews.PageQualityTests.test_normal_continuation_without_any_failure_adds_no_warning',
        'test_document_overviews.PageQualityTests.test_real_unreadable_region_is_preserved_alongside_a_continuation',
        'test_document_overviews.PageQualityTests.test_genuine_unreadability_is_not_cleared_by_the_words_page_break',
        'test_document_overviews.OverviewSelectionTests.test_short_document_uses_every_chunk_in_source_order',
        'test_document_overviews.OverviewSelectionTests.test_long_document_uses_broad_coverage_within_both_limits',
        'test_document_overviews.SavedOverviewTests.test_focused_question_includes_next_page_by_document_order',
        'test_document_overviews.SavedOverviewTests.test_focused_continuation_includes_previous_page',
        'test_document_overviews.SavedOverviewTests.test_true_unreadable_content_keeps_its_warning',
        'test_document_overviews.SavedOverviewTests.test_normal_continuation_has_no_incomplete_warning_in_llm_context',
        'test_document_overviews.SavedOverviewTests.test_limited_overview_is_not_labelled_extraction_failure',
    ],
}


def run_components():
    """Reuse existing assertions as named report entries rather than copying logic."""
    rows = []
    components = dict(COMPONENT_TESTS)
    # Keep focused regression fixes in the repeatable offline baseline too.
    from backend.tests.test_clarification_followups import ClarificationFollowupTests
    from backend.tests.test_warning_relevance import WarningRelevanceTests
    from backend.tests.test_visual_reprocessing import PendingVisualTests, VisualBackfillTests
    from backend.tests.test_module_document_matching import ModuleMatchingTests, ModuleConversationTests
    from backend.tests.test_conversation_intelligence import ConversationIntelligenceTests, AnswerDepthTests
    from backend.tests.test_conversation_history_api import HistoryAPITests
    from backend.tests.test_cached_fallback import CachedSyllabusTests, CachedFilePipelineTests, CacheStateTests
    from backend.tests.test_assignment_policy import PolicyTests, PolicyStreamingTests
    from backend.tests.test_conversation_management import ConversationManagementTests
    from backend.tests.test_conversation_management_api import ManagementAPITests
    for layer, classes in (
        ('clarification_followups', [ClarificationFollowupTests]),
        ('warning_relevance', [WarningRelevanceTests]),
        ('pending_visual_reprocessing', [PendingVisualTests, VisualBackfillTests]),
        ('module_document_matching', [ModuleMatchingTests, ModuleConversationTests]),
        ('conversation_intelligence', [ConversationIntelligenceTests, AnswerDepthTests]),
        ('conversation_history_api', [HistoryAPITests]),
        ('cached_content_fallback', [CachedSyllabusTests, CachedFilePipelineTests, CacheStateTests]),
        ('assignment_policy', [PolicyTests, PolicyStreamingTests]),
        ('conversation_management', [ConversationManagementTests, ManagementAPITests]),
    ):
        components[layer] = [cls.__module__.removeprefix('backend.tests.') + '.' + cls.__name__ + '.' + method
                             for cls in classes for method in unittest.defaultTestLoader.getTestCaseNames(cls)]
    for layer, names in components.items():
        for short in names:
            name = 'backend.tests.' + short
            result = unittest.TestResult()
            unittest.defaultTestLoader.loadTestsFromName(name).run(result)
            failures = [trace for _, trace in result.failures + result.errors]
            case = {'name':short,'category':layer,'question':'Component fixture; no student prompt',
                    'expected':{'behavior':'existing production assertions pass'}}
            rows.append(record(case, layer, {'test':name, 'assertions':'passed' if not failures else 'failed'}, failures))
    from backend.evaluation.visual_cases import VISUAL_CASES, evaluate_visual_case
    for name, native, visual in VISUAL_CASES:
        case = {'name':'visual_'+name, 'category':'visual_content', 'question':'Synthetic '+name+' page',
                'expected':{'vision_needed':visual is not None,'searchable':True,'page_number':1}}
        try:
            actual, failures = evaluate_visual_case(name, native, visual)
        except Exception as exc:
            actual, failures = {'error_type':type(exc).__name__}, ['Visual fixture pipeline failed.']
        rows.append(record(case, 'extraction', actual, failures,
            evidence='real PDF/detection/storage/search; fake transcription and vectors'))
    return rows


class ReadOnlyStore(IngestionStore):
    """Read the real saved store without constructor migrations or write access."""
    def __init__(self):
        from backend.app.canvas_service import get_cache_namespace
        self.namespace = get_cache_namespace()

    @contextlib.contextmanager
    def _connection(self):
        connection = sqlite3.connect(f'file:{DEFAULT_DATABASE}?mode=ro', uri=True)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
        finally:
            connection.close()


LIVE_CONVERSATIONS = (
    ('CSE 3311', (('How is Iteration 3 Presentation graded?', 'saved_assignment_rubric', 'rubric'),
                  ('what is the grading breakdown?', 'saved_assignment_rubric', 'rubric'),
                  ('how many points is each criterion worth?', 'saved_assignment_rubric', 'rubric'))),
    ('CSE 4321', (('explain homework 1', 'assignment_material', 'hw1_overview'),
                  ('what does part b mean?', 'assignment_material', 'focused'),
                  ('now explain countPositive', 'assignment_material', 'focused'),
                  ('how do I solve it?', 'assignment_material', 'focused'))),
    ('CSE 3302', (('what is being asked in homework 6', 'assignment_material', 'explanation'),
                  ('can you solve this assignment for me', 'assignment_material', 'policy'),
                  ('explain this assignment', 'assignment_material', 'explanation'))),
    ('CSE 3380', (('summarize Practice Questions 10', 'document_material', 'overview'),
                  ('explain question 7', 'document_material', 'focused'))),
)


async def run_live_answers():
    """Twelve actual turns on saved private courses; no indexing or ingestion writes."""
    from backend.app.llm_service import OpenAIAnswerProvider
    class ObservedProvider(OpenAIAnswerProvider):
        """Record actual provider inputs in private reports without altering them."""
        def __init__(self):
            super().__init__()
            self.plans, self.passages = [], []

        async def rewrite_query(self, course_id, question, context):
            plan = await super().rewrite_query(course_id, question, context)
            self.plans.append(copy.deepcopy(plan))
            return plan

        async def stream_generate(self, instructions, course_id, question, passages, context):
            self.passages = copy.deepcopy(passages)
            async with aclosing(super().stream_generate(instructions, course_id, question, passages, context)) as stream:
                async for event in stream:
                    yield event
    store, sessions, rows = ReadOnlyStore(), ConversationService(), []
    courses = [(cid, store.load_course(cid)) for cid in store.saved_current_course_ids()]
    for code, turns in LIVE_CONVERSATIONS:
        candidates = [(cid, snap) for cid, snap in courses if code.replace(' ', '-') in snap.get('course', {}).get('metadata', {}).get('course_code', '')]
        sid, cid = None, candidates[0][0] if len(candidates) == 1 else None
        for index, (question, route, kind) in enumerate(turns):
            case = {'name':f'{code.replace(" ", "_")}_{index+1}', 'category':kind, 'question':question,
                    'expected':{'retrieval_route':route,'behavior':kind}, 'conversation_context':'actual prior turns',
                    'recent_work_coverage':'routing/context covered; final live behavior evaluated separately'}
            if cid is None or candidates[0][1].get('ingestion_status') == 'running':
                rows.append(record(case, 'end_to_end_live', {}, ['Saved course missing, ambiguous or refreshing.'], status='blocked'))
                continue
            session = sessions.acquire(store.namespace, cid, sid)
            provider = ObservedProvider()
            events = [e async for e in sessions.events(session, question, str(index), store=store, provider=provider)]
            sid = session.id
            final = events[-1]
            result = final['data']
            if final['event'] != 'final':
                rows.append(record(case, 'end_to_end_live', {'course_id':cid,'events':events,'raw_plans':provider.plans,
                    'passages_to_answer_provider':provider.passages},
                    ['Provider/execution failure; no validated final answer.'], status='blocked', evidence='real model / private saved course'))
                continue
            failures = answer_failures(result, kind)
            if result.get('retrieval_route') != route:
                failures.append(f"Expected route {route}, got {result.get('retrieval_route')}")
            keys = source_keys(result)
            titles = [o.get('title', '') for s in result.get('retrieved_sources', []) for o in s['origins']]
            if kind == 'hw1_overview' and not any('hw1' in t.casefold().replace(' ', '') and 'question' in t.casefold() for t in titles):
                failures.append('Linked HW1 question file is missing from context.')
            likely_layer = 'answer generation/formatting'
            if result.get('retrieval_route') != route:
                likely_layer = 'query understanding / retrieval routing'
            elif not provider.passages:
                likely_layer = 'retrieval/context'
            elif kind == 'policy' and not any('not permitted' in p['text'].casefold() for p in provider.passages):
                likely_layer = 'retrieval: missing assignment policy'
            rows.append(record(case, 'end_to_end_live', {'course_id':cid,'response':result, 'source_keys':keys,
                'raw_plans':provider.plans,'passages_to_answer_provider':provider.passages,
                'sources_selected':titles, 'stream_delta_count':sum(e['event']=='answer_delta' for e in events),
                'active_assignments':session.active_assignments,'active_documents':session.active_documents}, failures,
                evidence='real model / private saved course', likely_layer=likely_layer))
    return rows


def render_report(report):
    """Produce a readable baseline with every failure and explicit untested boundaries."""
    lines = ['# Study Assistant baseline evaluation', '', f"Run: {report['timestamp']}. Mode: {report['mode']}.", '',
        'Production behavior was not changed. Public fixtures are synthetic. Private live answers remain in ignored reports.', '',
        '| Layer | Passed | Failed | Blocked |', '|---|---:|---:|---:|']
    for layer in sorted({r['layer'] for r in report['results']}):
        rows = [r for r in report['results'] if r['layer']==layer]
        lines.append(f"| {layer} | {sum(r['status']=='pass' for r in rows)} | {sum(r['status']=='fail' for r in rows)} | {sum(r['status']=='blocked' for r in rows)} |")
    lines += ['', 'A query_contract pass only checks declared fixtures. It is not a natural-language understanding score. '
        'Replay conversation passes test plumbing, not live answer quality. Guard probes expose accepted unsafe outputs; '
        'they do not claim a live model always produces that output.', '',
        'Not measured in this run: ' + '; '.join(report.get('unmeasured', [])), '',
        '## Failures and blocked checks', '']
    for row in report['results']:
        if row['status']=='pass':
            continue
        lines += [f"### {row['name']} ({row['status']})", '', f"Prompt: {row['question']}", '',
                  'Expected: ' + json.dumps(row['expected']), '', 'Evidence: ' + row['evidence_kind'], '',
                  'Likely layer: ' + row['likely_failing_layer'], '',
                  *['- '+reason for reason in row['reasons']], '', 'Actual:', '', '```json',
                  json.dumps(row['actual'], indent=2, ensure_ascii=True), '```', '']
    lines += ['## Limits and interpretation', '',
        '- Live checks use behavior heuristics, not exact answer wording or another paid LLM judge. Source entailment, mathematical correctness and policy nuance still need human review.',
        '- Visual fixtures/fake vision test detection and data flow, not real OCR recognition accuracy. Existing real visual evidence from earlier work is not counted as a new live pass.',
        '- The existing production intent solve_question represents the requested solve_assignment action; this task does not introduce a new taxonomy.',
        '- Date-only assignment explanations may need live Canvas to identify the assignment while remaining explain_assignment.',
        '- Normal baseline runs must retain failure exit codes. No expected-failure decorators or skipped known bugs turn red behavior green.',
        '- Full machine-readable observations and source/response details are in the companion JSON.', '']
    return '\n'.join(lines)


async def evaluate(mode):
    cases = load_cases()
    before = production_fingerprint()
    results = []
    if mode in ('offline', 'all', 'live-query'):
        results += await run_synthetic(cases, live=mode in ('all', 'live-query'))
    if mode in ('offline', 'all'):
        results += await run_conversation_replays(cases)
        results += await asyncio.to_thread(run_answer_guards, cases)
        results += await asyncio.to_thread(run_components)
    if mode in ('live-answers', 'all'):
        results += await run_live_answers()
    unmeasured = ['real Vision recognition accuracy']
    if mode == 'offline':
        unmeasured += ['real-model query understanding', 'real generated-answer quality on saved private courses']
    elif mode == 'live-query':
        unmeasured += ['real generated-answer quality on saved private courses']
    return {'timestamp':datetime.now(timezone.utc).isoformat(), 'mode':mode, 'dataset_cases':len(cases),
            'unmeasured':unmeasured,
            'dataset_sha256':hashlib.sha256(DATASET.read_bytes()).hexdigest(),
            'production_files_unchanged':before==production_fingerprint(),
            'production_fingerprint':before, 'results':results}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode', choices=('offline','live-query','live-answers','all'), default='offline')
    parser.add_argument('--allow-paid', action='store_true', help='Explicitly enable paid provider calls/private live source export.')
    parser.add_argument('--output', type=Path, default=ROOT / 'backend/data/reports/study-assistant-baseline')
    args = parser.parse_args()
    if args.mode!='offline' and not args.allow_paid:
        parser.error('Live modes require --allow-paid. They send data to OpenAI and use API credit.')
    report = asyncio.run(evaluate(args.mode))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.with_suffix('.json').write_text(json.dumps(report, indent=2, ensure_ascii=True)+'\n')
    args.output.with_suffix('.md').write_text(render_report(report))
    for layer in sorted({r['layer'] for r in report['results']}):
        rows = [r for r in report['results'] if r['layer']==layer]
        print(f"{layer}: {sum(r['status']=='pass' for r in rows)}/{len(rows)} passed; "
              f"{sum(r['status']=='fail' for r in rows)} failed; {sum(r['status']=='blocked' for r in rows)} blocked")
    for row in report['results']:
        if row['status']!='pass':
            print(row['status'].upper(), row['name'], row['likely_failing_layer'], '; '.join(row['reasons']))
    print('Report:', args.output.with_suffix('.md'))
    print('Production files unchanged:', report['production_files_unchanged'])
    return 1 if any(r['status']!='pass' for r in report['results']) or not report['production_files_unchanged'] else 0


if __name__ == '__main__':
    raise SystemExit(main())
