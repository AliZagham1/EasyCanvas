"""Deterministic assignment restrictions, independent of citation validation."""

import copy
import hashlib
import re

from backend.app.document_reference_service import solution_intent

AI = r'\b(?:generative\s+(?:AI|artificial intelligence)|GenAI|AI(?:[- ]generated)?|artificial intelligence|ChatGPT|language models?)\b'
DENIAL = r'(?:not\s+(?:permitted|allowed)|prohibited|forbidden|must\s+not|may\s+not|do\s+not|cannot|can.t)'
BAD_CONTROLS = re.compile(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]|\\(?:u0000|x00)')
FORMATS = r'pdf|docx?|pptx?|zip|txt|md|ipynb'


class PolicyViolationError(ValueError):
    """A generated answer conflicts with a backend-resolved assignment rule."""


def invalid_controls(text):
    """Allow Unicode, tabs and normal newlines; reject corrupt control output."""
    return isinstance(text, str) and bool(BAD_CONTROLS.search(text))


def restriction_kinds(text):
    """Recognize clear prohibitions, not a generic mention of AI or teamwork."""
    kinds = set()
    for sentence in re.split(r'[\n.!?;]+', text):
        # Parenthetical abbreviations and optional example lists do not change
        # the subject of a direct prohibition. Preserve original text in evidence.
        sentence = re.sub(r'\([^)]*\)', ' ', sentence)
        sentence = re.sub(r',\s*(?:such as|including)\b.*?,\s*(?=(?:is|are|must|may|shall)\b)', ' ', sentence, flags=re.I)
        sentence = re.sub(r'[,\s]+', ' ', sentence)
        if not re.search(r'\bnot (?:prohibited|forbidden|restricted)\b', sentence, re.I):
            if (re.search(AI + r'(?:\s+(?:tools?|use|usage|assistance|generated|work|answers?|solutions?|to|generate|assignment)){0,7}\s+(?:(?:is|are|remain|strictly)\s+)*' + DENIAL, sentence, re.I)
                    or re.search(DENIAL + r'\s+(?:use|using|employ|utilize|submit|generate)(?:\s+[\w-]+){0,5}\s+' + AI, sentence, re.I)
                    or re.search(r'\bno\s+(?:use\s+of\s+)?' + AI, sentence, re.I)):
                kinds.add('ai_generated_work_prohibited')
        if re.search(r'\bno\s+(?:outside|external)\s+(?:help|assistance)|\b(?:must not|may not|do not)\b.{0,35}\b(?:outside|external)\s+(?:help|assistance)', sentence, re.I):
            kinds.add('outside_assistance_prohibited')
        if re.search(r'\b(?:individual|independent) work only\b|\b(?:must|required to) work (?:alone|individually|independently)\b', sentence, re.I):
            kinds.add('individual_work_required')
    return sorted(kinds)


def _snippets(text):
    """Keep bounded exact instruction excerpts, including submission constraints."""
    for paragraph in re.split(r'\n\s*\n', text):
        kinds = restriction_kinds(paragraph)
        submission = bool(re.search(r'\b(?:submit|upload|submission)\b.{0,100}\b(?:must|only|required|pdf|docx|zip)\b|\b(?:must|only)\b.{0,80}\b(?:submit|upload)\b', paragraph, re.I | re.S))
        if not kinds and not submission:
            continue
        # Preserve the restriction itself when the source is one huge paragraph.
        for sentence in re.split(r'(?<=[.!?])\s+|\n', paragraph):
            rules = restriction_kinds(sentence)
            if rules or submission and re.search(r'\b(submit|upload|submission)\b', sentence, re.I):
                yield sentence.strip()[:1500], rules


def _locations(document, snippet):
    """Keep an instruction's existing page/slide/block reference when available."""
    for collection, kind in (('pages','pdf_page'),('slides','slide'),('rendered_pages','rendered_word_page'),('blocks','word_block'),('cells','notebook_cell')):
        for unit in document.get(collection) or []:
            if snippet in unit.get('text',''):
                return [{'kind':kind, **{k:unit[k] for k in ('page_number','slide_number','rendered_page_number','block_number','cell_number','location') if k in unit}}]
    return [{'kind':'assignment_instruction_text'}]


def attach_instructions(result, course_id, assignments, documents, instruction_keys):
    """Reserve instruction/policy evidence before competing solution passages.

    Only backend-resolved assignment records and their related non-solution
    instructions enter here. No IDs, URLs or rules are accepted from the model.
    """
    from backend.app.document_service import extract_html
    from backend.app.chunking_service import source_quality
    evidence, rules, seen = [], [], set()
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
            'status':'ok', 'warnings':[], 'source_quality':{'flags':[], 'policy_retrieval_eligible':True}}, None))
    for doc in documents:
        if (doc['document_key'] not in instruction_keys or doc.get('course_id',course_id) != course_id
                or doc.get('stale') or doc.get('content_usable') is False
                or doc.get('status') not in ('ok','partial') or not doc.get('text')
                or not source_quality(doc)['policy_retrieval_eligible']):
            continue
        linked = [a['canvas_id'] for a in assignments if any(r.get('source_type')=='assignment' and r.get('canvas_id')==a['canvas_id'] for r in doc.get('references') or [])]
        ids = linked or [a['canvas_id'] for a in assignments] or [None]
        for aid in ids:
            inputs.append((aid,doc['text'],{**{k:copy.deepcopy(doc.get(k)) for k in ('course_id','document_key','canvas_id','title','source_type','url','references','source_role','status','warnings','cache_status')},
                                           'locations':[{'kind':'assignment_instruction_text'}], 'source_quality':source_quality(doc)}, doc))
    for aid, text, origin, document in inputs:
        for snippet, kinds in _snippets(text):
            digest = hashlib.sha256(snippet.encode()).hexdigest()
            if (origin['document_key'],digest) in seen:
                continue
            seen.add((origin['document_key'],digest))
            for kind in kinds:
                rules.append({'kind':kind, 'assignment_id':aid, 'document_key':origin['document_key'], 'text_hash':digest})
            if re.search(r'\b(?:submit|submission|upload)\b',snippet,re.I) and re.search(r'\b(?:only|must|required)\b',snippet,re.I):
                formats = sorted(set(re.findall(r'\b('+FORMATS+r')\b',snippet,re.I)))
                if formats:
                    rules.append({'kind':'submission_format_required','allowed_formats':[f.casefold() for f in formats],
                                  'assignment_id':aid,'document_key':origin['document_key'],'text_hash':digest})
            source = {**origin,'source_kind':'assignment_instructions','chunk_index':0}
            if document:
                source['locations'] = _locations(document, snippet)
            evidence.append({'text':snippet, 'text_hash':digest, 'score':None, 'sources':[source], 'selection':'assignment_policy'})
    if not evidence:
        return result
    # Do not silently drop a restriction when a source is unusually long.
    # A policy-only safe response can still cite the relevant instruction.
    if len(evidence) > 12:
        required = []
        for kind in sorted({r['kind'] for r in rules}):
            relevant = {r['text_hash'] for r in rules if r['kind']==kind}
            first = next(e for e in evidence if e['text_hash'] in relevant)
            if first not in required:
                required.append(first)
        evidence = (required + [e for e in evidence if e not in required])[:12]
    hashes = {e['text_hash'] for e in evidence}
    result = {**result, 'results':evidence + [e for e in result['results'] if e['text_hash'] not in hashes],
              'assignment_policy':{'rules':[r for r in rules if r['text_hash'] in hashes], 'evidence_hashes':sorted(hashes)}}
    return result


def solution_restricted(policy):
    return any(r['kind'] in ('ai_generated_work_prohibited','outside_assistance_prohibited','individual_work_required') for r in (policy or {}).get('rules',[]))


def prohibited_request(question, understanding, policy):
    return solution_restricted(policy) and ((understanding or {}).get('intent')=='solve_question' or solution_intent(question))


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
    kinds = {r['kind'] for r in policy['rules']}
    if 'ai_generated_work_prohibited' in kinds:
        primary_kind = 'ai_generated_work_prohibited'
        reason = 'The assignment instructions prohibit using generative AI to produce the assigned work.'
    elif 'outside_assistance_prohibited' in kinds:
        primary_kind = 'outside_assistance_prohibited'
        reason = 'The assignment instructions prohibit outside assistance on the assigned work.'
    elif 'individual_work_required' in kinds:
        primary_kind = 'individual_work_required'
        reason = 'The assignment instructions require individual work.'
    else:
        primary_kind = 'submission_format_required'
        formats = sorted({f.upper() for r in policy['rules'] for f in r.get('allowed_formats',[])})
        reason = 'The assignment requires a submission in '+', '.join(formats)+' format.'
    hashes = {r['text_hash'] for r in policy['rules'] if r['kind']==primary_kind}
    ids = [sid for sid,source in sources.items() if source['text_hash'] in hashes]
    if not ids:
        raise PolicyViolationError('Assignment policy source is unavailable.')
    guidance = (' I cannot provide a submission-ready solution or rewrite the answers for you.' if solution_restricted(policy) else '')
    return {'status':'answered','blocks':[{'text':reason+guidance+' I can help explain the underlying course concepts or clarify what the instructions ask, within those restrictions.', 'source_ids':ids}], 'limitations':[]}
