"""Behavior assertions for evaluation reports; no production decision-making."""

import re

from backend.app.document_reference_service import resolve_assignment_reference, resolve_document_reference


def understanding_failures(case, plan, fixture):
    """Compare intent/entities separately from routing; aliases compare by identity."""
    expected = case['expected']
    failures = []
    for key in ('intent', 'requires_live_canvas', 'use_active_documents', 'question_number', 'part', 'deadline_reference'):
        if key in expected and str(plan.get(key, '')).casefold() != str(expected[key]).casefold():
            failures.append(f'{key}: expected {expected[key]!r}, got {plan.get(key)!r}')
    if not set(expected['requested_data']).issubset(plan.get('requested_data', [])):
        failures.append(f"requested_data missing {sorted(set(expected['requested_data']) - set(plan.get('requested_data', [])))}")
    if expected.get('assignment_reference'):
        match = resolve_assignment_reference(plan.get('assignment_reference', ''), fixture.assignments)
        target = resolve_assignment_reference(expected['assignment_reference'], fixture.assignments)
        identity = lambda r: r['assignment']['canvas_id'] if r['assignment'] else None
        if identity(match) != identity(target) or not plan.get('assignment_reference'):
            failures.append(f"assignment_reference did not identify {expected['assignment_reference']!r}")
    if expected.get('document_reference'):
        docs = fixture.store.load_documents(42)
        actual = resolve_document_reference(plan.get('document_reference', ''), docs)['document_keys']
        target = resolve_document_reference(expected['document_reference'], docs)['document_keys']
        if set(actual) != set(target) or not actual:
            failures.append(f"document_reference did not identify {expected['document_reference']!r}")
    return failures


def source_keys(result):
    if 'results' in result:
        return sorted({o['document_key'] for item in result['results'] for o in item['sources']})
    return sorted({o['document_key'] for item in result.get('retrieved_sources', []) for o in item['origins']})


def routing_failures(case, result):
    """Check real returned evidence, not just a matching route name."""
    expected, failures = case['expected'], []
    for key in ('retrieval_route', 'retrieval_mode', 'notice_status'):
        if key in expected and result.get(key) != expected[key]:
            failures.append(f'{key}: expected {expected[key]!r}, got {result.get(key)!r}')
    actual = set(source_keys(result))
    missing = set(expected.get('source_keys_required', [])) - actual
    forbidden = set(expected.get('source_keys_forbidden', [])) & actual
    if missing:
        failures.append(f'missing required evidence: {sorted(missing)}')
    if forbidden:
        failures.append(f'wrong document role: {sorted(forbidden)}')
    if expected.get('resolved_assignment_ids') and result.get('resolved_assignments') != expected['resolved_assignment_ids']:
        failures.append(f"resolved assignments: expected {expected['resolved_assignment_ids']}, got {result.get('resolved_assignments')}")
    if expected.get('policy_text_required'):
        texts = ' '.join(item['text'] for item in result.get('results', []))
        if not re.search(r'(?i)generative\s+AI.*not permitted', texts):
            failures.append('The actual AI-use restriction was not included in retrieved evidence.')
    if expected.get('retrieval_route') == 'saved_assignment_rubric':
        criteria = [r.get('structured_data', {}) for r in result.get('results', [])]
        if [c.get('points') for c in criteria] != [24, 24, 24, 28]:
            failures.append('Rubric point values were not preserved.')
        if any(not c.get('ratings') or not c['ratings'][0].get('long_description') for c in criteria):
            failures.append('Rubric rating descriptions were not preserved.')
    if expected.get('retrieval_mode') == 'overview':
        positions = [(s['document_key'], s['chunk_index']) for r in result.get('results', []) for s in r['sources']]
        for key in {p[0] for p in positions}:
            indices = [p[1] for p in positions if p[0] == key]
            if indices != sorted(indices):
                failures.append('Overview evidence is out of document order.')
    return failures


def answer_failures(result, kind):
    """Check explicit invariants, not exact prose; these are not a semantic grader."""
    failures = []
    text = result.get('answer', '')
    if result.get('status') != 'answered':
        failures.append(f"expected an answered response, got {result.get('status')!r}")
    known = {s['id'] for s in result.get('retrieved_sources', [])}
    for block in result.get('answer_blocks', []):
        if not block.get('source_ids') or not set(block['source_ids']).issubset(known):
            failures.append('A substantive block has missing/unknown citations.')
    if '\x00' in text or r'\u0000' in text:
        failures.append('Answer contains a NUL character or a literal NUL escape.')
    if text.count('```') % 2:
        failures.append('Answer contains an unclosed code fence.')
    if kind == 'policy':
        restriction = re.search(r'(?is)(not (?:allowed|permitted)|prohibit|cannot|can.t|may not|must not|restriction|not use)', text)
        if not restriction or not re.search(r'(?i)AI|generative|assignment.*rule|assignment.*polic', text):
            failures.append('Answer does not acknowledge the assignment AI-use restriction.')
        if not re.search(r'(?i)concept|explain|understand|guidance|clarify', text):
            failures.append('Answer does not offer permitted explanation/concept guidance.')
        if re.search(r'(?i)(?:def\s+\w+\s*\(|public\s+(?:static\s+)?\w+\s+\w+\s*\(|here is (?:the )?(?:complete )?submission.ready solution|complete solution is)', text):
            failures.append('Answer appears to provide a submission-ready solution despite the restriction.')
    if kind == 'hw1_overview':
        for name in ('findLast', 'countPositive'):
            if name not in text:
                failures.append('Assignment explanation omits ' + name)
        if not re.search(r'(?is)(?:not required|not assigned|exclude|do not need|don.t need).*?\(?f\)?|\(?f\)?.{0,45}(?:not required|not assigned|not need|exclude)', text):
            failures.append('Assignment explanation does not preserve the exclusion of part (f).')
    if kind == 'rubric':
        for name, points in (('Content', 24), ('Slides', 24), ('Presentation style', 24), ('Demo', 28)):
            if not re.search(re.escape(name) + r'.{0,100}\b' + str(points) + r'\b', text, re.I | re.S):
                failures.append(f'Rubric answer omits {name} with its correct {points} points.')
        if re.search(r'(?i)extra.credit|attendance.{0,20}\d+\s*points', text):
            failures.append('Rubric answer introduces an unsupported criterion.')
    if kind == 'overview' and re.search(r'(?i)extraction is incomplete|PDF.*incomplete|could not be read|cuts off', text):
        failures.append('A fully readable overview claims extraction is incomplete.')
    return failures
