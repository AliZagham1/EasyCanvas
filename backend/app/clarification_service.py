"""Resolve short replies only against backend-owned, conversation-local choices."""

import re

from backend.app.document_reference_service import name_tokens
from backend.app.query_understanding_service import empty_understanding

FACT_INTENTS = {'get_deadline', 'get_submission_status', 'get_assignment_details', 'explain_rubric'}
PLURAL_REPLIES = {'all', 'all of them', 'each one', 'every one', 'all three'}


def candidate_state(course_id, plan, records):
    """Retain only identity/title and the requested action, never Canvas secrets."""
    return {'course_id': course_id, 'intent': plan['intent'], 'requested_data': list(plan['requested_data']),
            'candidates': [{'assignment_id': a['canvas_id'], 'title': a.get('title')} for a in records]}


def reply_selection(question, state, snapshot, course_id):
    """Return a trusted selected ID list, a clarification, or None for a new topic."""
    text = ' '.join(token for token in name_tokens(question) if token not in {'please', 'thanks'})
    if not state or state.get('course_id') != course_id or state.get('intent') not in FACT_INTENTS:
        if text in PLURAL_REPLIES:
            return {'clarification': 'Which assignments do you mean? Please name an assignment or group.'}
        return None
    candidates = state['candidates']
    selected = None
    if text in PLURAL_REPLIES:
        if text == 'all three' and len(candidates) != 3:
            return {'clarification': 'Please confirm which items you mean; the current list does not contain three items.'}
        selected = candidates
    else:
        ordinal = re.fullmatch(r'(?:the )?(first|second|third|last)(?: one| item)?', text)
        if ordinal:
            position = {'first': 0, 'second': 1, 'third': 2, 'last': len(candidates)-1}[ordinal[1]]
            if position >= len(candidates):
                return {'clarification': 'That position is not in the current list. Please choose one of the listed items.'}
            selected = [candidates[position]]
        else:
            words = set(name_tokens(question)) - {'the', 'one', 'item', 'please'}
            matches = [a for a in candidates if words and words <= set(name_tokens(a.get('title') or ''))]
            if len(matches) == 1:
                selected = matches
            elif matches:
                return {'clarification': 'Please choose a more specific item from the current list.'}
    if selected is None:
        return None
    current = {a['canvas_id'] for a in snapshot.get('assignments', []) if a.get('course_id', course_id) == course_id}
    if any(a['assignment_id'] not in current for a in selected):
        return {'clarification': 'The available assignment list has changed. Please ask for the assignment again.'}
    return {'course_id': course_id, 'assignment_ids': [a['assignment_id'] for a in selected],
            'intent': state['intent'], 'requested_data': list(state['requested_data'])}


def reply_plan(question, selection):
    """Construct the existing intent contract; a short choice needs no model call."""
    if selection.get('clarification'):
        return empty_understanding(question, clarification=selection['clarification'])
    return empty_understanding(question, intent=selection['intent'], requested_data=selection['requested_data'])
