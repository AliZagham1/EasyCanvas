"""Deterministic assignment restrictions, independent of citation validation."""

import copy
import hashlib
import re

from backend.app.ai_use_policy import AI_RESTRICTIONS, LIMITED, PERMITTED, PROHIBITED, ai_statements, mentions_ai, work_types
from backend.app.document_reference_service import solution_intent

BAD_CONTROLS = re.compile(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]|\\(?:u0000|x00)')
FORMATS = r'pdf|docx?|pptx?|zip|txt|md|ipynb'
SOLUTION_RESTRICTIONS = (*AI_RESTRICTIONS, 'outside_assistance_prohibited', 'individual_work_required')
COURSE_RESTRICTS = 'course_restricts_assignment_permits'
ASSIGNMENT_RESTRICTS = 'assignment_restricts_course_permits'
CONFLICT_NOTICE = ("The course syllabus and this assignment's instructions give different AI-use rules. "
                   "The stricter rule is applied until your instructor clarifies.")


class PolicyViolationError(ValueError):
    """A generated answer conflicts with a backend-resolved assignment rule."""


def invalid_controls(text):
    """Allow Unicode, tabs and normal newlines; reject corrupt control output."""
    return isinstance(text, str) and bool(BAD_CONTROLS.search(text))


_LIST_ITEM = re.compile(r'\s*(?:[^\w\s"\'\u201c\u2018(\[]\s|\(?\d{1,2}[.)]\s|\(?[A-Za-z][.)]\s)')


def _unwrap(text):
    """Rejoin sentences that PDF extraction wrapped across lines.

    A line continues the previous one when it starts in lower case, or when the
    previous line is long and unfinished. Headings and list items stay separate.
    """
    lines = text.split('\n')
    joined = lines[:1]
    for line in lines[1:]:
        previous, current = joined[-1].rstrip(), line.lstrip()
        if (previous and current and not _LIST_ITEM.match(line) and not previous.endswith(('.', '!', '?', ':', ';'))
                and (current[0].islower() or len(previous) >= 60)):
            joined[-1] = previous + ' ' + current
        else:
            joined.append(line)
    return '\n'.join(joined)


def _sentences(text):
    """Split into sentences without breaking common abbreviations or wrapped lines."""
    return re.split(r'(?<!\be\.g\.)(?<!\bi\.e\.)(?<!\bA\.I\.)(?<=[.!?])\s+|\n+', _unwrap(text))


def _other_kinds(sentence):
    """Outside-assistance and individual-work rules (unchanged from before EC-015)."""
    kinds = set()
    for part in re.split(r'[.!?;]+', sentence):
        part = re.sub(r'[,\s]+', ' ', re.sub(r'\([^)]*\)', ' ', part))
        if re.search(r'\bno\s+(?:outside|external)\s+(?:help|assistance)|\b(?:must not|may not|do not)\b.{0,35}\b(?:outside|external)\s+(?:help|assistance)', part, re.I):
            kinds.add('outside_assistance_prohibited')
        if re.search(r'\b(?:individual|independent) work only\b|\b(?:must|required to) work (?:alone|individually|independently)\b', part, re.I):
            kinds.add('individual_work_required')
    return kinds


def _analyze(text):
    """Yield (sentence, restriction kinds, AI statements); "it" follows an AI tool named just before."""
    carry = False
    for sentence in _sentences(text):
        statements = ai_statements(sentence, carry=carry)
        kinds = _other_kinds(sentence) | {s['kind'] for s in statements if s['kind'] in AI_RESTRICTIONS}
        carry = mentions_ai(sentence) or (carry and bool(statements))
        yield sentence, sorted(kinds), statements


def restriction_kinds(text):
    """Recognize clear prohibitions, not a generic mention of AI or teamwork."""
    return sorted({kind for _, kinds, _ in _analyze(text) for kind in kinds})


def _snippets(text, *, course=False):
    """Keep bounded exact instruction excerpts, including submission constraints.

    Yields (snippet, restriction kinds, AI statements, listed). A sentence that
    only permits AI is not `listed`: it becomes evidence only when it explains
    an exception or a conflict. Course-wide text contributes AI statements only.
    """
    for paragraph in re.split(r'\n\s*\n', text):
        analysed = list(_analyze(paragraph))
        submission = not course and bool(re.search(r'\b(?:submit|upload|submission)\b.{0,100}\b(?:must|only|required|pdf|docx|zip)\b|\b(?:must|only)\b.{0,80}\b(?:submit|upload)\b', paragraph, re.I | re.S))
        if not submission and not any(kinds or statements for _, kinds, statements in analysed):
            continue
        # Preserve the restriction itself when the source is one huge paragraph.
        for sentence, kinds, statements in analysed:
            if course:
                kinds = [kind for kind in kinds if kind in AI_RESTRICTIONS]
                if statements:
                    yield sentence.strip()[:1500], kinds, statements, bool(kinds)
            else:
                listed = bool(kinds) or submission and bool(re.search(r'\b(submit|upload|submission)\b', sentence, re.I))
                if listed or statements:
                    yield sentence.strip()[:1500], kinds, statements, listed


def _locations(document, snippet):
    """Keep an instruction's existing page/slide/block reference when available."""
    for collection, kind in (('pages','pdf_page'),('slides','slide'),('rendered_pages','rendered_word_page'),('blocks','word_block'),('cells','notebook_cell')):
        for unit in document.get(collection) or []:
            # A rejoined sentence still belongs to the page/slide that holds its wrapped lines.
            if snippet in unit.get('text','') or ' '.join(snippet.split()) in ' '.join(unit.get('text','').split()):
                return [{'kind':kind, **{k:unit[k] for k in ('page_number','slide_number','rendered_page_number','block_number','cell_number','location') if k in unit}}]
    return [{'kind':'assignment_instruction_text'}]


def _effective(restrictions, permits):
    """A rule that defers to authorization is met by an explicit permission in the same source.

    "Unauthorized use of AI is a breach" does not restrict a syllabus that goes on
    to say "this course permits the use of AI". A flat prohibition is never lifted.
    """
    return [(item, statement) for item, statement in restrictions if not (permits and statement['allows_exceptions'])]


def course_policy_keys(documents):
    """Syllabus documents whose AI-use statements apply across the course."""
    return {doc['document_key'] for doc in documents
            if doc.get('source_type') in ('syllabus', 'syllabus_file') or doc.get('source_role') == 'course_syllabus'}


def _usable_policy_document(doc, course_id):
    from backend.app.chunking_service import source_quality
    return (doc.get('course_id', course_id) == course_id and not doc.get('stale') and doc.get('content_usable') is not False
            and doc.get('status') in ('ok', 'partial') and bool(doc.get('text')) and source_quality(doc)['policy_retrieval_eligible'])


def _assignment_types(assignment):
    raw = assignment.get('metadata') or {}
    types = work_types(assignment.get('title') or '')
    if raw.get('is_quiz_assignment') or 'online_quiz' in (raw.get('submission_types') or []):
        types.add('quiz')
    return types


def attach_instructions(result, course_id, assignments, documents, instruction_keys, *, policy_keys=(), work_titles=(), assigned_work=False):
    """Reserve instruction/policy evidence before competing solution passages.

    Only backend-resolved assignment records and their related non-solution
    instructions enter here. No IDs, URLs or rules are accepted from the model.

    `policy_keys` names syllabus documents. Their AI-use statements are
    course-wide rules: they apply to each resolved assignment (or, with no
    native assignment, to `work_titles` when that is assigned work), limited to
    a work type when the syllabus says so. An assignment's own restriction
    always applies. An assignment that permits AI lifts a course-wide rule only
    when the syllabus itself allows authorized exceptions; otherwise the two
    conflict and the course-wide rule is enforced.
    """
    from backend.app.document_service import extract_html
    from backend.app.chunking_service import source_quality
    inputs = []
    for assignment in assignments:
        raw = assignment.get('metadata') or {}
        text = extract_html(raw.get('description') or '')['text']
        extensions = raw.get('allowed_extensions')
        if isinstance(extensions,list) and extensions and all(isinstance(e,str) and re.fullmatch(FORMATS,e,re.I) for e in extensions):
            text += '\n\nSubmission files must use '+', '.join(extensions)+' format.'
        inputs.append((assignment['canvas_id'], text, {
            'course_id':course_id, 'document_key':f"assignment:{assignment['canvas_id']}",
            'canvas_id':assignment['canvas_id'], 'title':assignment.get('title'), 'source_type':'assignment',
            'url':assignment.get('url') or raw.get('html_url'), 'locations':[{'kind':'canvas_assignment_instructions'}],
            'status':'ok', 'warnings':[], 'source_quality':{'flags':[], 'policy_retrieval_eligible':True}}, None, 'assignment'))

    def origin(doc, scope):
        return {**{k:copy.deepcopy(doc.get(k)) for k in ('course_id','document_key','canvas_id','title','source_type','url','references','source_role','status','warnings','cache_status')},
                'locations':[{'kind':'assignment_instruction_text'}], 'source_quality':source_quality(doc), 'policy_scope':scope}

    for doc in documents:
        if doc['document_key'] not in instruction_keys or not _usable_policy_document(doc, course_id):
            continue
        linked = [a['canvas_id'] for a in assignments if any(r.get('source_type')=='assignment' and r.get('canvas_id')==a['canvas_id'] for r in doc.get('references') or [])]
        ids = linked or [a['canvas_id'] for a in assignments] or [None]
        for aid in ids:
            inputs.append((aid, doc['text'], origin(doc, 'assignment'), doc, 'assignment'))
    for doc in documents:
        if doc['document_key'] in policy_keys and doc['document_key'] not in instruction_keys and _usable_policy_document(doc, course_id):
            inputs.append((None, doc['text'], origin(doc, 'course'), doc, 'course'))

    items, rules, seen = [], [], set()
    for aid, text, source_origin, document, scope in inputs:
        for snippet, kinds, statements, listed in _snippets(text, course=scope == 'course'):
            digest = hashlib.sha256(snippet.encode()).hexdigest()
            if (source_origin['document_key'],digest) in seen:
                continue
            seen.add((source_origin['document_key'],digest))
            source = {**source_origin,'source_kind':'assignment_instructions','chunk_index':0}
            if document:
                source['locations'] = _locations(document, snippet)
            items.append({'assignment_id':aid, 'scope':scope, 'kinds':kinds, 'statements':statements, 'listed':listed and scope == 'assignment',
                          'document_key':source_origin['document_key'], 'text_hash':digest,
                          'evidence':{'text':snippet, 'text_hash':digest, 'score':None, 'sources':[source], 'selection':'assignment_policy'}})
            if scope == 'course':
                continue
            for kind in kinds:
                if kind not in AI_RESTRICTIONS:      # AI rules are resolved per assignment below
                    rules.append({'kind':kind, 'assignment_id':aid, 'document_key':source_origin['document_key'], 'text_hash':digest, 'scope':'assignment'})
            if re.search(r'\b(?:submit|submission|upload)\b',snippet,re.I) and re.search(r'\b(?:only|must|required)\b',snippet,re.I):
                formats = sorted(set(re.findall(r'\b('+FORMATS+r')\b',snippet,re.I)))
                if formats:
                    rules.append({'kind':'submission_format_required','allowed_formats':[f.casefold() for f in formats],
                                  'assignment_id':aid,'document_key':source_origin['document_key'],'text_hash':digest,'scope':'assignment'})

    # Resolve course-wide AI rules against each requested piece of work.
    course_items = [item for item in items if item['scope'] == 'course']
    conflicts, exceptions, explained = [], [], []
    targets = ([(a['canvas_id'], _assignment_types(a), True) for a in assignments]
               or [(None, set().union(*(work_types(title) for title in work_titles)) if work_titles else set(), assigned_work)])
    for aid, types, assigned in targets:
        assigned = assigned or bool(types)
        def applies(statement):
            return bool(set(statement['work_types']) & types) if statement['work_types'] else assigned
        own = [item for item in items if item['scope'] == 'assignment' and item['assignment_id'] == aid]
        own_permits = [item for item in own if any(s['kind'] == PERMITTED for s in item['statements'])]
        own_restricts = _effective([(item, s) for item in own for s in item['statements'] if s['kind'] in AI_RESTRICTIONS], own_permits)
        course_permits = [item for item in course_items if any(s['kind'] == PERMITTED and applies(s) for s in item['statements'])]
        course_restricts = _effective([(item, s) for item in course_items for s in item['statements']
                                       if s['kind'] in AI_RESTRICTIONS and applies(s)], course_permits)
        for item, statement in own_restricts:
            rule = {'kind':statement['kind'], 'assignment_id':aid, 'document_key':item['document_key'], 'text_hash':item['text_hash'],
                    'scope':'assignment', 'allows_exceptions':statement['allows_exceptions']}
            if rule not in rules:
                rules.append(rule)
        own_restricts = [item for item, _ in own_restricts]
        enforced = course_restricts
        if own_restricts:
            if course_permits and not course_restricts:
                conflicts.append({'assignment_id':aid, 'direction':ASSIGNMENT_RESTRICTS,
                                  'restriction_hashes':sorted({i['text_hash'] for i in own_restricts}),
                                  'permission_hashes':sorted({i['text_hash'] for i in course_permits})})
                explained += course_permits
        elif own_permits and course_restricts:
            record = {'assignment_id':aid, 'restriction_hashes':sorted({i['text_hash'] for i, _ in course_restricts}),
                      'permission_hashes':sorted({i['text_hash'] for i in own_permits})}
            explained += own_permits
            if all(s['allows_exceptions'] for _, s in course_restricts):
                # The syllabus delegates exceptions and this assignment grants one.
                exceptions.append(record)
                explained += [item for item, _ in course_restricts]
                enforced = []
            else:
                conflicts.append({**record, 'direction':COURSE_RESTRICTS})
        for item, statement in enforced:
            rule = {'kind':statement['kind'], 'assignment_id':aid, 'document_key':item['document_key'], 'text_hash':item['text_hash'],
                    'scope':'course', 'allows_exceptions':statement['allows_exceptions']}
            if rule not in rules:
                rules.append(rule)

    ruled = {(rule['document_key'], rule['text_hash']) for rule in rules}
    evidence, included = [], set()
    for item in items:
        if item['text_hash'] not in included and (item['listed'] or (item['document_key'], item['text_hash']) in ruled or item in explained):
            included.add(item['text_hash'])
            evidence.append(item['evidence'])
    if not evidence:
        return result
    # Do not silently drop a restriction when a source is unusually long.
    # A policy-only safe response can still cite the relevant instruction.
    if len(evidence) > 12:
        required = []
        needed = [{r['text_hash'] for r in rules if (r['kind'], r['scope']) == pair} for pair in sorted({(r['kind'], r['scope']) for r in rules})]
        needed += [set(record[side]) for record in conflicts + exceptions for side in ('restriction_hashes', 'permission_hashes')]
        for relevant in needed:
            first = next((e for e in evidence if e['text_hash'] in relevant), None)
            if first and first not in required:
                required.append(first)
        evidence = (required + [e for e in evidence if e not in required])[:12]
    hashes = {e['text_hash'] for e in evidence}
    policy = {'rules':[r for r in rules if r['text_hash'] in hashes], 'evidence_hashes':sorted(hashes)}
    if conflicts:
        policy['conflicts'] = conflicts
    if exceptions:
        policy['exceptions'] = exceptions
    return {**result, 'results':evidence + [e for e in result['results'] if e['text_hash'] not in hashes], 'assignment_policy':policy}


def solution_restricted(policy):
    return any(r['kind'] in SOLUTION_RESTRICTIONS for r in (policy or {}).get('rules',[]))


def work_request(question):
    """A plain request to produce the assigned work itself, in the student's own words."""
    if re.search(r"\b(?:don'?t|do not|without|not)\b.{0,25}\b(?:write|solve|do|complete|code|answers?|solutions?)\b", question, re.I):
        return False
    if re.search(r'\b(?:do|complete|finish|write|code|implement|generate|produce)\b.{0,60}\bfor me\b', question, re.I):
        return True
    if re.match(r'\s*(?:how|what|why|when|where|which|should i|do i|can i|am i)\b', question, re.I):
        return False
    return bool(re.search(
        r'\b(?:do|complete|finish)\s+(?:my|the|this|that)\s+(?:entire\s+|whole\s+)?(?:homework|hw|assignment|lab|project|essay|paper|report|quiz|exam)\b'
        r'|\bwrite\s+(?:my|the|this|that|an?|me\s+(?:an?|the))\s+(?:entire\s+|whole\s+|full\s+|complete\s+)?(?:essay|paper|report|code|program|function|implementation|solution|answer|response|submission)\b'
        r'|\b(?:generate|produce|give me|provide)\s+(?:the\s+|an?\s+)?(?:complete\s+|full\s+|final\s+|working\s+)?(?:code|program|implementation|essay|submission)\b',
        question, re.I))


def solution_request(question, understanding):
    """The student asks for the work product: model intent, solution wording, or a plain request to do the work."""
    return (understanding or {}).get('intent')=='solve_question' or solution_intent(question) or work_request(question)


def prohibited_request(question, understanding, policy):
    """Decided before generation. Naming a solutions file is a solution request however it is worded."""
    return solution_restricted(policy) and (bool((policy or {}).get('solution_material')) or solution_request(question, understanding))


def validate_response(answer, policy):
    """Reject obvious solution leakage even when every citation is valid.

    This is a conservative guard for clear restrictions, not a semantic proof
    that arbitrary prose follows every possible instructor rule.
    """
    if not policy or answer['status']!='answered':
        return
    text = '\n'.join(b['text'] for b in answer['blocks'])
    for rule in policy['rules']:
        if rule['kind']=='submission_format_required':
            for sentence in re.split(r'\n|(?<=[.!?])\s+',text):
                if re.search(r'\b(?:submit|upload)\b',sentence,re.I) and not re.search(r'\b(?:not|never|cannot|don.t)\b',sentence,re.I):
                    formats = {f.casefold() for f in re.findall(r'\b('+FORMATS+r')\b',sentence,re.I)}
                    if formats - set(rule['allowed_formats']):
                        raise PolicyViolationError('Generated submission format conflicts with assignment instructions.')
    if solution_restricted(policy) and re.search(r'```|\bdef\s+\w+\s*\(|\bpublic\s+(?:static\s+)?\w+\s+\w+\s*\(|\b(?:here is|here.s)\s+(?:the |a )?(?:complete |submission.ready )?(?:solution|answer|code)|\b(?:corrected|fixed)\s+(?:code|implementation)|\b(?:the answer|complete solution) is\b',text,re.I):
        raise PolicyViolationError('Generated response conflicts with assignment assistance restrictions.')


def compliant_answer(policy, sources):
    """Generate a cited safe alternative in backend code, without another model."""
    rules = policy['rules']
    kinds = {r['kind'] for r in rules}
    conflicts = policy.get('conflicts') or []
    ai_rules = [r for r in rules if r['kind'] in AI_RESTRICTIONS]
    course_conflicts = [c for c in conflicts if c['direction'] == COURSE_RESTRICTS]
    if course_conflicts:
        # Never assume permission: the stricter course-wide rule stands until clarified.
        hashes = {h for c in course_conflicts for side in ('restriction_hashes', 'permission_hashes') for h in c[side]}
        reason = ("The course syllabus restricts using generative AI for course work, but this assignment's instructions appear to allow it. "
                  'Because the two conflict, I am applying the stricter course policy until your instructor clarifies which rule applies.')
    elif ai_rules:
        own = [r for r in ai_rules if r.get('scope', 'assignment') == 'assignment']
        selected = own or ai_rules
        prohibited = [r for r in selected if r['kind'] == PROHIBITED] or selected
        hashes = {r['text_hash'] for r in prohibited}
        where = 'assignment instructions restrict' if own else 'course syllabus restricts'
        if all(r.get('allows_exceptions') for r in selected):
            # e.g. "Unauthorized use of AI is a breach": say what the rule says, not more.
            reason = (f'The {where} generative AI unless the instructor authorizes it, '
                      'and I did not find an authorization that covers producing this work.')
        elif prohibited[0]['kind'] == PROHIBITED:
            reason = ('The assignment instructions prohibit using generative AI to produce the assigned work.' if own else
                      'The course syllabus prohibits using generative AI to produce assigned work, and this assignment does not state an exception.')
        else:
            reason = ('The assignment instructions allow generative AI only for limited purposes, which do not include producing the assigned work.' if own else
                      'The course syllabus allows generative AI only for limited purposes, which do not include producing assigned work.')
        stricter = [c for c in conflicts if c['direction'] == ASSIGNMENT_RESTRICTS]
        if own and stricter:
            hashes |= {h for c in stricter for h in c['permission_hashes']}
            reason += " The course syllabus allows some AI use, but this assignment's own restriction applies here."
    elif 'outside_assistance_prohibited' in kinds:
        hashes = {r['text_hash'] for r in rules if r['kind'] == 'outside_assistance_prohibited'}
        reason = 'The assignment instructions prohibit outside assistance on the assigned work.'
    elif 'individual_work_required' in kinds:
        hashes = {r['text_hash'] for r in rules if r['kind'] == 'individual_work_required'}
        reason = 'The assignment instructions require individual work.'
    else:
        hashes = {r['text_hash'] for r in rules if r['kind'] == 'submission_format_required'}
        formats = sorted({f.upper() for r in rules for f in r.get('allowed_formats',[])})
        reason = 'The assignment requires a submission in '+', '.join(formats)+' format.'
    ids = [sid for sid,source in sources.items() if source['text_hash'] in hashes]
    if not ids:
        raise PolicyViolationError('Assignment policy source is unavailable.')
    guidance = (' I cannot provide a submission-ready solution or rewrite the answers for you.' if solution_restricted(policy) else '')
    if policy.get('matched_work'):
        reason = 'Your question closely matches assigned work in this course. ' + reason
    return {'status':'answered','blocks':[{'text':reason+guidance+' I can help explain the underlying course concepts or clarify what the instructions ask, within those restrictions.', 'source_ids':ids}], 'limitations':[]}
