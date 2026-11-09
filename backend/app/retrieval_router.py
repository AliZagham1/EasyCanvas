"""Choose sources of truth from a validated intent; never ask a model for IDs."""

import re
from datetime import datetime, timezone

import requests

from backend.app import retrieval_service, structured_canvas_service as structured
from backend.app.document_reference_service import (
    resolve_assignment_reference, resolve_document_reference, document_role, numbered_references, name_tokens,
)
from backend.app.embedding_service import default_store
from backend.app.llm_service import LLMProviderError
from backend.app.query_understanding_service import validate_understanding
from backend.app.clarification_service import candidate_state, FACT_INTENTS
from backend.app.conversation_state_service import interpret_turn, entity_matches, title_words
from backend.app.assignment_policy_service import attach_instructions


def _standalone_concept(question):
    """Recognize an independent definition request from the student's own words."""
    text = question.strip()
    match = re.fullmatch(r'(?is)(?:what (?:is|are)|explain|define|describe)\s+(.+?)[?.!\s]*', text)
    if not match:
        return False
    subject = match[1].strip()
    if not subject or len(subject) > 100:
        return False
    if re.search(r'\b(my|our|your|this|that|these|those|it|its|they|their|previous|earlier|above|same|again|second|third|next)\b', text, re.I):
        return False
    if re.search(r'\b(course|class|canvas|assignment|homework|lab|module|document|pdf|file|syllabus|lecture|slides|quiz|questions?|problems?|exercise|part|section|rubric|attendance|policy|deadline|due|submission|submitted|points|requirement|instructions?)\b', text, re.I):
        return False
    if re.search(r'\b[A-Z]{2,5}\s*\d{3,4}\b|\b(?:hw|lab)\s*#?\s*\d+\b', text, re.I):
        return False
    return True


def _correct_standalone_concept(question, plan):
    """Remove only model-added course context from a clear new concept topic."""
    if plan['intent'] not in ('general_concept', 'course_wide_question', 'explain_question',
                              'explain_document', 'find_course_material', 'explain_assignment'):
        return plan
    if plan['operation'] != 'topic_switch':
        return plan
    if not _standalone_concept(question):
        return plan
    actual_words = set(name_tokens(question))
    references = [plan[key] for key in ('course_reference', 'assignment_reference', 'document_reference') if plan[key]]
    if not references:
        # A course-wide concept may intentionally use retrieved lecture material.
        return plan
    descriptors = {'assignment', 'homework', 'lab', 'project', 'presentation', 'written', 'pdf', 'doc', 'docx', 'ppt', 'pptx'}
    if all((set(name_tokens(reference)) - descriptors) <= actual_words for reference in references):
        # The student named this entity; it was not added only by the model.
        return plan
    plan.update(intent='general_concept', query=question.strip(), operation='topic_switch',
                entity_type='', included_references=[], excluded_references=[],
                requested_data=[], requires_live_canvas=False, use_active_documents=False,
                clarification='')
    for key in ('course_reference', 'assignment_reference', 'document_reference',
                'assignment_number', 'homework_number', 'question_number', 'part', 'deadline_reference'):
        plan[key] = ''
    return plan


def _notice(course_id, plan, message, *, clarify=False, route="clarification", status=None):
    return {"course_id": course_id, "status": "no_content", "results": [], "resolved_documents": [],
            "query_understanding": plan, "retrieval_route": route, "notice": message,
            "notice_status": status or ("clarification_needed" if clarify else "insufficient_context")}


def _selected_course(reference, snapshot):
    if not reference:
        return True
    metadata = snapshot.get("course", {}).get("metadata", {})
    requested = name_tokens(reference)
    actual = name_tokens((metadata.get("course_code") or "") + " " + (metadata.get("name") or ""))
    return bool(requested) and all(token in actual for token in requested)


def _general_concept_allowed(question, plan, context):
    """Admit only a self-contained concept question with no course dependency."""
    prior_general = (context.get('conversation_state') or {}).get('current_intent') == 'general_concept'
    general_followup = (prior_general and plan['operation'] == 'continue'
                        and not context.get('active_documents') and not context.get('active_assignments')
                        and bool(context.get('recent_turns')))
    if plan['intent'] != 'general_concept' or (plan['use_active_documents'] and not general_followup):
        return False
    if plan['operation'] != 'topic_switch' and not general_followup:
        return False
    if any(plan[field] for field in ('course_reference', 'assignment_reference', 'document_reference',
                                     'assignment_number', 'homework_number', 'question_number', 'part', 'deadline_reference')):
        return False
    if plan['included_references'] or plan['excluded_references'] or plan['requires_live_canvas']:
        return False
    text = question.casefold()
    if re.search(r'\b(my|our|this|your)\s+(course|class|assignment|homework|lab|project|pdf|document|file|lecture|syllabus|professor|instructor)\b', text):
        return False
    if re.search(r'\b(canvas|due|deadline|submit|submission|grade|graded|points|rubric|attendance|policy|syllabus|homework|assignment|instructor|professor|lecture|slides|pdf|document|file|lab\s*\d+|hw\s*#?\s*\d+)\b', text):
        return False
    if re.search(r'\b[A-Z]{2,5}\s*\d{3,4}\b', question):
        return False
    if re.search(r'\b(it|that|this|these|those|previous|above|earlier|again)\b', text) and context.get('recent_turns') and not general_followup:
        return False
    if general_followup and re.match(r'\s*(?:explain (?:that|it)|simplify|give (?:me )?(?:an? )?example|say (?:that|it)|what do you mean)', text):
        return True
    return bool(re.match(r'\s*(?:what (?:is|are|does)|how (?:does|do|can)|why (?:does|do|is|are)|explain|define|describe|give (?:me )?an? example of)\b', text))


def _course_policy_request(question, intent):
    """Keep course rules away from similarly named assignment or grade items."""
    if intent in FACT_INTENTS or intent in ('list_assignments', 'solve_question', 'study_plan'):
        return False
    text = question.casefold()
    if re.search(r'\b(assignment|homework|grade item|points|due|deadline|submitted|submission|rubric)\b', text):
        return False
    return bool(re.search(r'\b(policy|policies|rules|attendance|absences?|late work|make.?up work|academic integrity)\b', text))


def _syllabus_documents(documents):
    """Select current readable syllabus documents, including canonical file copies."""
    return [doc['document_key'] for doc in documents
            if (doc.get('source_role') == 'course_syllabus' or doc.get('source_type') == 'syllabus_file')
            and doc.get('content_usable') and doc.get('text')]


def _readable_document_keys(keys, documents):
    """Follow a deduplicated file alias to its current readable document."""
    by_key = {doc['document_key']: doc for doc in documents}
    resolved = []
    for key in keys:
        doc = by_key.get(key)
        if not doc:
            continue
        if not doc.get('content_usable'):
            doc = by_key.get(doc.get('canonical_document_key'))
        if doc and doc.get('content_usable') and doc.get('text'):
            resolved.append(doc['document_key'])
    return list(dict.fromkeys(resolved))


def _assignment_choices(course_id, plan, records):
    """Publish and retain the same ordered candidate set for the next reply."""
    result = _notice(course_id, plan, 'Which item do you mean? ' + '; '.join(a.get('title') or 'Untitled assignment' for a in records), clarify=True)
    if plan['intent'] in FACT_INTENTS and len(records) <= 12:
        result['clarification_candidates'] = candidate_state(course_id, plan, records)
    return result


def _active_assignment_ids(context, assignments):
    ids = {a["canvas_id"] for a in assignments}
    active = {item.get("assignment_id") for item in context.get("active_assignments", [])}
    for doc in context.get("active_documents", []):
        key = doc.get("document_key", "")
        if re.fullmatch(r"assignment:\d+", key):
            active.add(int(key.split(":")[1]))
    return active & ids


def _empty_current_list(course_id, text, now):
    origin = {"course_id": course_id, "document_key": f"course:{course_id}:assignments", "canvas_id": course_id,
              "source_type": "assignment_collection", "source_kind": "structured_canvas", "title": "Current Canvas assignments",
              "url": None, "chunk_index": 0, "locations": [{"kind": "canvas_assignment_list"}], "status": "ok", "warnings": [],
              "source_quality": {"flags": [], "policy_retrieval_eligible": True}}
    return [structured.evidence_item(text, origin, {"matching_assignments": [], "checked_at": now.isoformat()})]


def _assignment_list(course_id, question, plan, snapshot, context, now):
    """Answer lists from live Canvas entities, applying explicit negative filters."""
    try:
        records = structured.current_assignments(course_id)
    except requests.RequestException:
        raise LLMProviderError('Current Canvas information is unavailable. Please retry.') from None
    plan['requires_live_canvas'] = True
    if plan['entity_type'] == 'homework':
        records = [a for a in records if 'homework' in title_words(a.get('title') or '')]
    for reference in plan['included_references']:
        records = entity_matches(reference, records)
    excluded = set()
    for reference in plan['excluded_references']:
        matches = entity_matches(reference, records)
        if len(matches) > 1:
            return _notice(course_id, plan, 'Which item should I exclude? ' + '; '.join(a['title'] for a in matches), clarify=True)
        excluded.update(a['canvas_id'] for a in matches)
    if plan['operation'] == 'exclude':
        excluded.update((context.get('conversation_state') or {}).get('excluded_assignment_ids', []))
    records = [a for a in records if a['canvas_id'] not in excluded]
    results = structured.current_fact_evidence(course_id, records, ['assignment_title'], structured.course_timezone(snapshot), now)
    if not results:
        results = _empty_current_list(course_id, 'No current Canvas assignments match that selection.', now)
    return {'course_id':course_id, 'status':'ready', 'results':results, 'direct_response':True,
            'resolved_documents':[f"assignment:{a['canvas_id']}" for a in records],
            'resolved_assignments':[a['canvas_id'] for a in records], 'excluded_assignment_ids':sorted(excluded),
            'query_understanding':plan, 'retrieval_route':'current_assignment_list'}


def _retain_assignment_instructions(result, course_id, records, docs, snapshot):
    """Keep policy-bearing linked instructions regardless of the selected role."""
    instruction_keys = set()
    for record in records:
        linked = [d for d in docs if any(ref.get('source_type')=='assignment' and ref.get('canvas_id')==record['canvas_id'] for ref in d.get('references') or [])]
        instructions = resolve_document_reference(record['title'], docs, modules=snapshot.get('modules'))
        instruction_keys.update(d['document_key'] for d in linked if document_role(d) != 'solution')
        instruction_keys.update(instructions['document_keys'])
    return attach_instructions(result, course_id, records, docs, instruction_keys)


def route_query(course_id, question, understanding, *, store=None, provider=None, conversation_context=None, now=None):
    """Resolve real course records, then select one retrieval route.

    Deadline/submission/points routes always refresh Canvas regardless of the
    model's live-data suggestion. Rubrics use saved structured fields. Document
    routes reuse existing indexing, quality checks and bounded context builders.
    """
    context = conversation_context or {}
    plan = interpret_turn(question, validate_understanding(understanding), context)
    plan["requires_live_canvas"] = False  # The backend decides which route actually refreshes data.
    intent = plan["intent"]
    if plan['clarification'] and intent not in FACT_INTENTS:
        return _notice(course_id, plan, plan['clarification'], clarify=True)
    question_number = re.search(r"\b(?:question|exercise|problem)\s*#?\s*(\d+)\b", question, re.I)
    if question_number and plan["question_number"] and int(question_number[1]) != int(plan["question_number"]):
        return _notice(course_id, plan, "Please confirm the question number; it could not be resolved safely.", clarify=True)
    if intent == "study_plan":
        return _notice(course_id, plan, "Study-plan generation is not implemented yet. I can explain assignments and course material.", route="study_plan_unavailable")
    focus = [d["document_key"] for d in context.get("active_documents", [])] if plan["use_active_documents"] else []
    store = store or default_store()
    snapshot = store.load_course(course_id)
    if snapshot is None:
        raise ValueError("No saved course snapshot for this account/course.")
    if plan['intent'] in ('general_concept', 'course_wide_question', 'explain_question',
                          'explain_document', 'find_course_material', 'explain_assignment'):
        plan = _correct_standalone_concept(question, plan)
        intent = plan['intent']
    material_updating = snapshot.get("ingestion_status") == "running"
    if not _selected_course(plan['course_reference'], snapshot):
        return _notice(course_id, plan, 'Please select the course named in your question.', clarify=True)
    if intent == 'general_concept':
        if _general_concept_allowed(question, plan, context):
            return {'course_id': course_id, 'status': 'ready', 'results': [], 'resolved_documents': [],
                    'query_understanding': plan, 'retrieval_route': 'general_concept'}
        # A mistaken general classification must not bypass course grounding.
        plan['intent'] = 'course_wide_question'
        intent = plan['intent']
    if intent == 'list_assignments':
        return _assignment_list(course_id, question, plan, snapshot, context, now or datetime.now(timezone.utc))
    # Course-wide knowledge still uses the existing course-scoped search.
    if (intent == "course_wide_question" and not _course_policy_request(question, intent)
            and not plan["assignment_reference"] and not plan["document_reference"] and not plan["course_reference"]):
        if material_updating:
            return _notice(course_id, plan, "This course's saved material is being refreshed. Please retry shortly.",
                           route="course_material_refresh", status="indexing_needed")
        options = {"focus_documents": focus} if focus else {}
        result = retrieval_service.retrieve_evidence(course_id, plan["query"], store=store, provider=provider, **options)
        return {**result, "query_understanding": plan, "retrieval_route": "course_material"}
    if not _selected_course(plan["course_reference"], snapshot):
        return _notice(course_id, plan, "That question names a different course. Please select that course before asking.", clarify=True)
    assignments = [a for a in snapshot.get("assignments", []) if a.get("course_id", course_id) == course_id]
    active = _active_assignment_ids(context, assignments) if plan["use_active_documents"] else set()
    reference = (plan["assignment_reference"] or ("homework " + plan["homework_number"] if plan["homework_number"] else "")
                 or ("assignment " + plan["assignment_number"] if plan["assignment_number"] else ""))
    if not reference and plan['operation'] in ('select', 'correct') and len(plan['included_references']) == 1:
        reference = plan['included_references'][0]
    if not reference and plan['clarification'] and intent in FACT_INTENTS:
        families = numbered_references(question)
        if len(families) == 1:
            kind, number = next(iter(families))
            reference = f'{kind} {number}'
    original_numbers = numbered_references(question)
    parsed_numbers = numbered_references(reference)
    if original_numbers and parsed_numbers and not parsed_numbers.issubset(original_numbers):
        return _notice(course_id, plan, "Please confirm the assignment number; the reference could not be resolved safely.", clarify=True)
    resolution = resolve_assignment_reference(reference, assignments, active)
    if reference and (resolution['status'] == 'missing' or resolution['status'] == 'ambiguous' and plan['use_active_documents']):
        matches = entity_matches(reference, assignments, context.get('active_assignments', []) if plan['use_active_documents'] else [])
        if len(matches) == 1:
            resolution = {'status':'matched', 'assignment':matches[0], 'candidate_ids':[matches[0]['canvas_id']], 'candidates':[matches[0]['title']]}
        elif matches:
            return _assignment_choices(course_id, plan, matches)
    record = resolution["assignment"]
    if intent == 'explain_rubric' and plan['use_active_documents'] and not record and len(active) == 1:
        record = next(a for a in assignments if a['canvas_id'] in active)
    if plan['operation'] == 'exclude' and intent != 'list_assignments':
        recent = (context.get('conversation_state') or {}).get('recent_entities', [])
        pool = [a for a in assignments if a['canvas_id'] in {e['assignment_id'] for e in recent}]
        excluded = {a['canvas_id'] for ref in plan['excluded_references'] for a in entity_matches(ref, pool)}
        choices = [a for a in pool if a['canvas_id'] not in excluded]
        if len(choices) != 1:
            return _notice(course_id, plan, 'Which other assignment do you mean?', clarify=True)
        record = choices[0]
        reference = record['title']
    if record and plan['use_active_documents'] and intent in FACT_INTENTS:
        plan['assignment_reference'] = record['title']
        plan['query'] = intent.replace('_', ' ') + ' for ' + record['title']
    selection = context.get('clarification_selection') or {}
    selected_records = []
    if selection:
        ids = selection.get('assignment_ids', [])
        by_id = {a['canvas_id']: a for a in assignments}
        if (selection.get('course_id') != course_id or selection.get('intent') != intent
                or intent not in FACT_INTENTS or not ids or len(ids) > 12 or any(aid not in by_id for aid in ids)):
            return _notice(course_id, plan, 'Please select an available assignment again.', clarify=True)
        selected_records = [by_id[aid] for aid in ids]
        record = selected_records[0]
    if resolution['status'] == 'ambiguous' and intent in FACT_INTENTS and not selected_records:
        return _assignment_choices(course_id, plan, [a for a in assignments if a['canvas_id'] in resolution['candidate_ids']])
    if plan['clarification']:
        return _notice(course_id, plan, plan['clarification'], clarify=True)
    docs = store.load_documents(course_id)
    now = now or datetime.now(timezone.utc)
    zone = structured.course_timezone(snapshot)
    direct = intent in ("get_deadline", "get_submission_status", "get_assignment_details")
    date_reference = plan["deadline_reference"]
    if date_reference and reference and not record:
        return _notice(course_id, plan, "Which assignment do you mean? " + "; ".join(resolution["candidates"][:6])
                       if resolution["status"] == "ambiguous" else "I could not find that assignment in this course.",
                       clarify=resolution["status"] == "ambiguous")
    try:
        if direct and not reference and re.search(r'\bnext\b', question, re.I):
            plan['requires_live_canvas'] = True
            records = [a for a in structured.current_assignments(course_id) if structured.due_in_window(a['metadata'], (now, datetime.max.replace(tzinfo=timezone.utc)))
                       and (a['metadata'].get('submission') or {}).get('workflow_state', 'unsubmitted') == 'unsubmitted']
            records.sort(key=lambda a: datetime.fromisoformat(a['metadata']['due_at'].replace('Z', '+00:00')))
            records = records[:1]
        elif direct and selected_records:
            plan['requires_live_canvas'] = True
            records = [structured.current_assignment(course_id, a) for a in selected_records]
        elif direct and record:
            plan["requires_live_canvas"] = True
            records = [structured.current_assignment(course_id, record)]
        elif date_reference and not record:
            try:
                window = structured.date_window(date_reference, now, zone)
            except ValueError:
                return _notice(course_id, plan, "Please specify today, tomorrow, a weekday, this week, next week, or a date as YYYY-MM-DD.", clarify=True)
            plan["requires_live_canvas"] = True
            matches = [a for a in structured.current_assignments(course_id) if structured.due_in_window(a["metadata"], window)]
            matches.sort(key=lambda a: datetime.fromisoformat(a["metadata"]["due_at"].replace("Z", "+00:00")))
            if direct and not reference:
                records = matches
            elif len(matches) == 1:
                record = matches[0]
            else:
                if matches:
                    return _assignment_choices(course_id, plan, matches)
                names = "; ".join(a.get("title") or "Untitled assignment" for a in matches[:6])
                return _notice(course_id, plan, ("Which assignment do you mean? " + names) if matches
                               else "No Canvas assignment has a deadline in that window.", clarify=bool(matches))
        elif direct:
            return _notice(course_id, plan, "Which assignment do you mean?" if not reference else "I could not find that assignment in this course.", clarify=not reference)
    except requests.RequestException:
        raise LLMProviderError("Current Canvas information is unavailable. Please retry.") from None
    if direct:
        requested = {"get_deadline": ["deadline"], "get_submission_status": ["submission_status"]}.get(intent)
        requested = requested or [value for value in plan["requested_data"] if value in ("points", "assignment_title")]
        results = structured.current_fact_evidence(course_id, records, requested, zone, now)
        if not results:
            text = 'No future unsubmitted Canvas assignments were found.' if re.search(r'\bnext\b', question, re.I) else 'No Canvas assignments have a deadline in that window.'
            results = _empty_current_list(course_id, text, now)
        return {"course_id": course_id, "status": "ready", "results": results, "direct_response": True,
                "resolved_documents": [f"assignment:{a['canvas_id']}" for a in records],
                "resolved_assignments": [a["canvas_id"] for a in records], "query_understanding": plan,
                "retrieval_route": "current_assignment_facts"}
    if intent == "explain_rubric":
        plan["requested_data"] = ["rubric"]
        if not record:
            candidates = [a for a in assignments if a.get("metadata", {}).get("rubric")]
            if not reference and not active and not context.get("active_documents") and len(candidates) == 1:
                record = candidates[0]
            else:
                names = "; ".join(resolution["candidates"][:6])
                return _notice(course_id, plan, "Which assignment's rubric do you mean?" + (" " + names if names else ""), clarify=True)
        results = [item for a in selected_records or [record] for item in structured.rubric_evidence(course_id, a, snapshot['synced_at'])]
        if not results:
            return _notice(course_id, plan, "Canvas did not provide a saved rubric for this assignment.", route="saved_assignment_rubric")
        plan["requires_live_canvas"] = False
        return {"course_id": course_id, "status": "ready", "results": results,
                "resolved_documents": [f"assignment:{a['canvas_id']}" for a in selected_records or [record]],
                "resolved_assignments": [a['canvas_id'] for a in selected_records or [record]],
                "query_understanding": plan, "retrieval_route": "saved_assignment_rubric"}
    if material_updating:
        return _notice(course_id, plan, "This course's saved material is being refreshed. Please retry shortly.",
                       route="course_material_refresh", status="indexing_needed")
    if _course_policy_request(question, intent):
        syllabus_keys = _syllabus_documents(docs)
        explicit = resolve_document_reference(plan['document_reference'], docs, modules=snapshot.get('modules')) if plan['document_reference'] else None
        matched_keys = (explicit['document_keys'] + explicit['supporting_document_keys']) if explicit and explicit['status'] == 'matched' else []
        explicit_keys = _readable_document_keys(matched_keys, docs)
        keys = explicit_keys or syllabus_keys
        options = {'document_keys': keys} if keys else {}
        result = retrieval_service.retrieve_evidence(course_id, plan['query'], store=store, provider=provider, **options)
        return {**result, 'query_understanding': plan, 'retrieval_route': 'course_policy_material'}
    assignment_intent = intent == "explain_assignment" or bool(reference) or bool(active and not plan["document_reference"])
    if assignment_intent and record:
        linked = [d for d in docs if any(ref.get("source_type") == "assignment" and ref.get("canvas_id") == record["canvas_id"]
                                       for ref in d.get("references") or [])]
        linked_for_role = [d for d in linked if (document_role(d) == 'solution') == (intent == 'solve_question')]
        named = resolve_document_reference(("solution for " if intent == "solve_question" else "") + record["title"], docs, modules=snapshot.get('modules'))
        if named.get('module_ids') and named['status'] == 'ambiguous' and not linked_for_role:
            return _notice(course_id, plan, 'Which module do you mean? ' + '; '.join(named['candidates']), clarify=True)
        named_files = [d for d in docs if d['document_key'] in named['document_keys']
                       and not (linked_for_role and named.get('module_ids'))]
        files = [d for d in linked + named_files if document_role(d) == "solution"] if intent == "solve_question" else []
        if not files:
            files = [d for d in linked + named_files if document_role(d) != "solution"]
        keys = list(dict.fromkeys([f"assignment:{record['canvas_id']}"] + [d["document_key"] for d in files]))
        mode = "focused" if intent in ("explain_question", "solve_question") or plan["part"] or plan["question_number"] else "overview"
        result = retrieval_service.retrieve_evidence(course_id, plan["query"], store=store, provider=provider,
                                                     document_keys=keys, retrieval_mode=mode)
        # Policy-bearing question/instruction files remain available even when
        # the student requests a separately indexed solution document.
        result = _retain_assignment_instructions(result, course_id, [record], docs, snapshot)
        return {**result, "resolved_assignments": [record["canvas_id"]], "query_understanding": plan,
                "retrieval_route": "assignment_material"}
    if assignment_intent and reference and not record:
        # A file-only assignment can still be known even when no native record is visible.
        file_match = resolve_document_reference(reference, docs, modules=snapshot.get('modules'))
        if file_match.get('module_ids') and file_match['status'] == 'ambiguous':
            return _notice(course_id, plan, 'Which module do you mean? ' + '; '.join(file_match['candidates']), clarify=True)
        if file_match["status"] != "matched":
            return _notice(course_id, plan, "Which assignment do you mean? " + "; ".join(resolution["candidates"][:6])
                           if resolution["status"] == "ambiguous" else "I could not find that assignment in this course.",
                           clarify=resolution["status"] == "ambiguous")
    document_reference = plan["document_reference"] or reference or question
    explicit = resolve_document_reference(document_reference, docs, focus, modules=snapshot.get('modules'))
    original = resolve_document_reference(question, docs, modules=snapshot.get('modules'))
    if original["document_keys"]:
        explicit = original
    if plan["document_reference"] and explicit["status"] == "none":
        return _notice(course_id, plan, "I could not find that document in this course.")
    if explicit["status"] in ("unresolved", "ambiguous"):
        return _notice(course_id, plan, "Please specify the document title." if explicit["status"] == "ambiguous"
                       else "I could not find that document in this course.", clarify=explicit["status"] == "ambiguous")
    keys = explicit["document_keys"] + explicit["supporting_document_keys"]
    mode = "overview" if intent in ("explain_document", "summarize_document") else "focused"
    options = {"document_keys": keys, "retrieval_mode": mode} if keys else {"focus_documents": focus}
    result = retrieval_service.retrieve_evidence(course_id, plan["query"], store=store, provider=provider, **options)
    related_assignments = [a for a in assignments if f"assignment:{a['canvas_id']}" in keys]
    if related_assignments:
        result = _retain_assignment_instructions(result, course_id, related_assignments, docs, snapshot)
        result['resolved_assignments'] = [a['canvas_id'] for a in related_assignments]
    elif keys and (assignment_intent or explicit.get('family') or explicit.get('module_ids')):
        instruction_keys = set(explicit['document_keys'])
        if explicit.get('family'):
            kind,number = explicit['family']
            instructions = resolve_document_reference(f'{kind} {number}',docs,modules=snapshot.get('modules'))
            instruction_keys.update(instructions['document_keys'])
        instruction_keys = {d['document_key'] for d in docs if d['document_key'] in instruction_keys and document_role(d) != 'solution'}
        result = attach_instructions(result,course_id,[],docs,instruction_keys)
    return {**result, "query_understanding": plan, "retrieval_route": "document_material" if keys else "course_material"}
