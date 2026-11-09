"""Small, deterministic conversational operations over actual course entities."""

import copy
import re

from backend.app.document_reference_service import name_tokens, REFERENCE_ALIASES


def title_words(text):
    return {REFERENCE_ALIASES.get(word, word) for word in name_tokens(text)}


def entity_matches(reference, records, active=()):
    """Match title words, keeping numbers/types distinct; never invent aliases."""
    words = title_words(reference) - {'the', 'one', 'please', 'no', 'and', 'what', 'about'}
    if not words:
        return []
    matches = [r for r in records if words <= title_words(r.get('title') or '')]
    if len(matches) > 1 and active:
        old = set().union(*(title_words(a.get('title') or '') for a in active)) - {'homework', 'assignment', 'presentation', 'written', 'deliverable'}
        related = [r for r in matches if old & title_words(r.get('title') or '')]
        if related:
            matches = related
    return matches


def interpret_turn(question, plan, context):
    """Preserve operations that the provider may omit, without changing entity IDs."""
    plan = copy.deepcopy(plan)
    state = context.get('conversation_state') or {}
    text = question.casefold().strip()
    prior = state.get('current_intent')
    relational = bool(re.match(r'(what about|how about|and\b|also\b|no\b|the other|explain (?:that|it)|give me the short|i don.t understand)', text))
    explicit = bool(re.search(r'\b(due|deadline|graded|grading|rubric|solve|explain|summarize|summarise|submission)\b', text))
    if relational and not explicit and prior in ('get_deadline', 'get_submission_status', 'get_assignment_details', 'explain_rubric'):
        plan.update(intent=prior, use_active_documents=True, operation='continue', clarification='')
    if re.search(r'\bhow (?:much|many)\b', text) and prior == 'explain_rubric':
        plan.update(intent='explain_rubric', use_active_documents=True, operation='continue', clarification='')
    restyle = bool(re.search(r'\b(explain (?:that|it) simpler|short version|explain simpler|don.t understand|more detail)\b', text))
    if restyle and prior and context.get('recent_turns'):
        plan.update(intent=prior, use_active_documents=True, operation='continue', clarification='')
        plan['part'] = plan['part'] or state.get('active_part', '')
        plan['question_number'] = plan['question_number'] or state.get('active_question', '')
    negative = re.search(r'\b(?:other than|except|besides)\s+(.+)', text)
    other = bool(re.search(r'\b(anything else|what else|another one|not that one|any other homework|other one)\b', text))
    listing = (bool(re.search(r'\b(homeworks?|assignments?|items)\b', text))
               and bool(re.search(r'\b(what|which|list|any|there|else|other)\b', text))
               and bool(re.search(r'\b(homeworks|assignments|items|have|list|there|other)\b', text))
               and not re.search(r'\b(explain|solve|question|part|due|deadline|graded|grading|rubric|points)\b', text))
    if negative or other or listing or plan['intent'] == 'list_assignments':
        explain_other = bool(re.search(r'\b(explain|tell me about)\b', text)) and other
        if not explain_other:
            if listing and not negative and not other and plan['assignment_reference']:
                plan['included_references'] = list(dict.fromkeys(plan['included_references'] + [plan['assignment_reference']]))
            plan.update(intent='list_assignments', assignment_reference='', document_reference='', clarification='')
        plan['entity_type'] = ('homework' if re.search(r'\bhomeworks?\b', text) else
                               plan.get('entity_type') or state.get('entity_type') or 'assignment')
        if negative:
            target = negative[1].strip(' .?!')
            if target not in ('that', 'that one', 'it'):
                plan['excluded_references'] = list(dict.fromkeys(plan['excluded_references'] + [target]))
            else:
                other = True
        if negative or other:
            plan.update(operation='exclude', use_active_documents=True)
            if other and not plan['excluded_references']:
                entities = (state.get('recent_entities') if prior == 'list_assignments' else context.get('active_assignments')) or state.get('recent_entities') or []
                plan['excluded_references'] = [e['title'] for e in entities if e.get('title')]
                if not entities:
                    plan['clarification'] = 'Which item should I exclude? Please name an assignment.'
        elif listing:
            plan['operation'] = 'topic_switch'
    if re.match(r'(?:no\s+)?(?:the\s+)?(?:written|presentation)\s*(?:one)?[?.!]*$', text):
        plan.update(use_active_documents=True, operation='correct' if text.startswith('no') else 'select')
    correction = re.match(r'^no[,.]?\s+(?:the\s+)?(.+?)[.!?]*$', question, re.I)
    if correction and prior and not plan['assignment_reference']:
        plan.update(operation='correct', use_active_documents=True, assignment_reference=correction[1])
    return plan


def next_state(previous, question, result, snapshot):
    """Retain backend-resolved identities and current action, not generated facts."""
    plan = result.get('query_understanding') or {}
    if not plan:
        return previous
    continuing = plan.get('use_active_documents', False)
    state = copy.deepcopy(previous) if continuing else {}
    by_id = {a['canvas_id']: a for a in snapshot.get('assignments', [])}
    ids = result.get('resolved_assignments', [])
    entities = [{'assignment_id': aid, 'title': by_id[aid].get('title')} for aid in ids if aid in by_id]
    if entities and set(ids) <= {e['assignment_id'] for e in previous.get('recent_entities', [])}:
        state['recent_entities'] = previous['recent_entities']
        state['entity_type'] = previous.get('entity_type', '')
    state.update(current_intent=plan['intent'], entity_type=plan.get('entity_type') or state.get('entity_type', ''),
                 topic=plan['query'][:600],
                 operation=plan.get('operation'), exclusions=plan.get('excluded_references', []),
                 excluded_assignment_ids=result.get('excluded_assignment_ids', []),
                 active_part=plan.get('part') or state.get('active_part', ''),
                 active_question=plan.get('question_number') or state.get('active_question', ''),
                 active_rubric=ids if plan['intent'] == 'explain_rubric' else [],
                 active_modules=sorted({name for s in result.get('retrieved_sources', []) for o in s['origins'] for name in o.get('module_names', [])}))
    if entities and all('homework' in title_words(e.get('title') or '') for e in entities):
        state['entity_type'] = 'homework'
    if result.get('retrieval_route') == 'current_assignment_list':
        state['recent_entities'] = entities
    elif entities and not continuing and not state.get('recent_entities'):
        state['recent_entities'] = entities
    state['selected_entities'] = entities
    return state


def answer_context(context, plan):
    """Strip unrelated turns on a topic switch; keep bounded reference context."""
    result = copy.deepcopy(context)
    if not plan.get('use_active_documents'):
        result.update(recent_turns=[], active_documents=[], active_assignments=[], conversation_state={})
    return result
