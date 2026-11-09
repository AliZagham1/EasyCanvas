"""Resolve student references against actual course records, without model calls."""

import re
import unicodedata
from pathlib import PurePath


# Aliases apply to numbered references, never to arbitrary document contents.
REFERENCE_ALIASES = {
    "hw": "homework", "homework": "homework",
    "assignment": "assignment", "assign": "assignment", "assn": "assignment",
    "lab": "lab", "laboratory": "lab",
    "project": "project", "proj": "project", "quiz": "quiz",
    "iteration": "iteration", "iter": "iteration",
}
ROLE_WORDS = {"question", "questions", "instruction", "instructions", "solution", "solutions",
              "answer", "answers", "key", "worksheet", "handout"}


def name_tokens(value):
    """Normalize typography and word/number boundaries, keeping every number."""
    value = unicodedata.normalize("NFKC", value)
    value = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1 \2", value)
    value = re.sub(r"([a-z])([A-Z])", r"\1 \2", value).casefold()
    value = re.sub(r"(?<=\w)['’](?=\w)", "", value)
    value = re.sub(r"([a-z])([0-9])|([0-9])([a-z])", lambda m: " ".join(x for x in m.groups() if x), value)
    return re.findall(r"\w+", value.replace("_", " "))


def numbered_references(value):
    tokens = name_tokens(value)
    return {(REFERENCE_ALIASES[token], str(int(tokens[i + 1])))
            for i, token in enumerate(tokens[:-1])
            if token in REFERENCE_ALIASES and tokens[i + 1].isascii() and tokens[i + 1].isdigit()}


def solution_intent(question):
    """An explanation/part question is not implicitly a request for solutions."""
    if re.search(r"\b(without|no|not|dont|don't)\b.{0,25}\b(solutions?|answers?|solve)\b", question, re.I):
        return False
    return bool(re.search(
        r"\bsolutions?\b|\bsolve\b|\b(show|give|provide|tell)\b.{0,35}\banswers?\b"
        r"|\banswer\s+(for|to|part|question|homework|hw)\b|\bwhat('s| is)\s+(the\s+)?answer\b"
        r"|\b(fix|repair|debug)\b|\b(what|where)\b.{0,20}\b(wrong|bug|fault)\b",
        question, re.I))


def document_role(document):
    tokens = set(name_tokens(PurePath(document.get("title") or "").stem))
    if tokens & {"solution", "solutions", "answer", "answers"}:
        return "solution"
    return "questions" if tokens & {"question", "questions", "instruction", "instructions", "worksheet"} else "document"


def _filename_matches(question, documents):
    """Keep exact names, extensions, and non-homework numbered filenames working."""
    query = " " + " ".join(name_tokens(question)) + " "
    extensions = set(re.findall(r"\.(pdf|docx?|pptx?|txt|md|ipynb)\b", question.casefold()))
    candidates = []
    for doc in documents:
        title = doc.get("title") or ""
        if doc.get("source_type") not in ("file", "syllabus_file"):
            continue
        if extensions and PurePath(title).suffix.casefold().lstrip(".") not in extensions:
            continue
        tokens = name_tokens(PurePath(title).stem)
        if not tokens:
            continue
        if len(tokens) == 1 and (not extensions or " " + " ".join(name_tokens(title)) + " " not in query):
            continue
        if " " + " ".join(tokens) + " " in query:
            candidates.append((100 + len(tokens), doc))
            continue
        for index, token in enumerate(tokens):
            if token.isdigit() and index >= 1:
                if index == 1 and len(token) == 4 and tokens[0].isalpha() and 2 <= len(tokens[0]) <= 5:
                    continue
                if " " + " ".join(tokens[:index + 1]) + " " in query:
                    candidates.append((50 + index, doc))
                break
    return candidates


def _qualifiers(document, family):
    """Different named HW1 families must not silently collapse into one."""
    tokens = name_tokens(PurePath(document.get("title") or "").stem)
    return tuple(token for token in tokens
                 if token not in ROLE_WORDS
                 and not (token in REFERENCE_ALIASES and REFERENCE_ALIASES[token] == family[0])
                 and not (token.isdigit() and str(int(token)) == family[1]))


def _canonical_name(value):
    """Compare actual labels with the existing aliases and exact numeric tokens."""
    return tuple(((token.lstrip('0') or '0') if token.isascii() and token.isdigit() else REFERENCE_ALIASES.get(token, token))
                 for token in name_tokens(value))


def _module_ids(document):
    ids = [document.get('module_id'), *(ref.get('module_id') for ref in document.get('references') or [])]
    return {value for value in ids if type(value) is int and value > 0}


def module_catalog(documents, modules=None):
    """Use current module titles when supplied; otherwise use saved file provenance."""
    if modules is not None:
        records = modules
    else:
        records = [{'canvas_id': ref.get('module_id'), 'title': ref.get('module_name')}
                   for doc in documents for ref in [doc, *(doc.get('references') or [])]]
    catalog = {}
    courses = {d.get('course_id') for d in documents if d.get('course_id') is not None}
    for record in records:
        if record.get('course_id') is not None and courses and record['course_id'] not in courses:
            continue
        mid = record.get('canvas_id') or record.get('module_id') or record.get('id')
        title = record.get('title') or record.get('module_name') or record.get('name')
        if type(mid) is int and mid > 0 and isinstance(title, str) and title.strip():
            catalog[mid] = {'canvas_id': mid, 'title': title}
    return catalog


def document_module_names(document, catalog):
    """Expose only current, course-scoped module labels for this source's IDs."""
    return sorted({catalog[mid]['title'] for mid in _module_ids(document) if mid in catalog})


def _module_match(reference, documents, modules=None):
    """Resolve one actual module, then select only files referenced by its ID."""
    if set(name_tokens(reference)) <= {'explain','summarize','summarise','this','that','the','pdf','document','file','it','please','me','you','can','could','all'}:
        return {'status':'missing','candidate_ids':[], 'module_ids':[], 'files':[], 'candidates':[]}
    # Question/page numbers are secondary references, not part of a lab's identity.
    if numbered_references(reference):
        reference = re.sub(r'\b(?:questions?|exercises?|problems?|parts?|pages?|slides?)\s*#?\s*(?:\d+|[a-z]\b)', ' ', reference, flags=re.I)
    catalog = module_catalog(documents, modules)
    match = resolve_assignment_reference(reference, catalog.values())
    ids = match['candidate_ids']
    files = [d for d in documents if d.get('source_type') in ('file','syllabus_file') and _module_ids(d) & set(ids)]
    return {**match, 'module_ids': ids, 'files': files}


def _supporting_assignments(selected, documents, family, modules=None, module_ids=None):
    ids = {ref.get("canvas_id") for doc in selected for ref in doc.get("references") or []
           if ref.get("source_type") == "assignment" and ref.get("canvas_id") is not None}
    assignments = [doc for doc in documents if doc.get("source_type") == "assignment"]
    linked = [doc for doc in assignments if doc.get("canvas_id") in ids]
    catalog = module_catalog(documents, modules)
    mids = set(module_ids) if module_ids is not None else {mid for doc in selected for mid in _module_ids(doc)}
    labels = [record['title'] for mid, record in catalog.items() if mid in mids]
    if not linked:
        matches = [doc for doc in assignments if any(_canonical_name(doc.get('title') or '') == _canonical_name(label) for label in labels)]
        # A file shared by several work modules does not choose their requirements for the student.
        if len(matches) == 1:
            linked = matches
        elif matches:
            return []
    module_families = {ref for label in labels for ref in numbered_references(label)}
    if not linked and family and not (module_families and family not in module_families):
        matching = [doc for doc in assignments if family in numbered_references(doc.get("title") or "")]
        if len(matching) == 1:
            linked = matching
    return sorted(doc["document_key"] for doc in linked)


def resolve_document_reference(question, documents, active_documents=(), *, modules=None):
    """Return best file role/family, linked assignment IDs, or explicit ambiguity.

    The caller supplies only this account/course's current records. Exact file
    names outrank aliases; numbered family matches require exact type and number.
    Multiple equally plausible named families are reported, never guessed.
    """
    documents = list(documents)
    files = [d for d in documents if d.get("source_type") in ("file", "syllabus_file")]
    references = numbered_references(question)
    matches = _filename_matches(question, files)
    exact = [d for score, d in matches if score >= 100]
    role = "solution" if solution_intent(question) else "questions"
    result = {"status": "none", "document_keys": [], "supporting_document_keys": [],
              "role": role, "family": None, "candidates": []}
    # An explicit full filename is an intentional request, even for a solution.
    explicit_extension = bool(re.search(r"\.(pdf|docx?|pptx?|txt|md|ipynb)\b", question, re.I))
    module = _module_match(question, documents, modules) if not explicit_extension else None
    if module and module['status'] == 'ambiguous':
        return {**result, 'status':'ambiguous', 'module_ids':module['module_ids'], 'candidates':module['candidates']}
    if module and module['status'] == 'matched':
        preferred = [d for d in module['files'] if (document_role(d) == 'solution') == (role == 'solution')]
        if not preferred:
            return {**result, 'status':'unresolved', 'module_ids':module['module_ids'], 'candidates':module['candidates']}
        families = numbered_references(module['assignment']['title'])
        family = next(iter(families)) if len(families) == 1 else None
        return {**result, 'status':'matched', 'document_keys':sorted({d['document_key'] for d in preferred}),
                'module_ids':module['module_ids'], 'family':list(family) if family else None,
                'supporting_document_keys':_supporting_assignments(preferred, documents, family, modules, module['module_ids'])}
    if exact and (explicit_extension or not references):
        best = max(score for score, _ in matches)
        selected = [d for score, d in matches if score == best]
    elif references:
        if len(references) != 1:
            return {**result, "status": "ambiguous"}
        family = next(iter(references))
        result["family"] = list(family)
        candidates = [d for d in files if family in numbered_references(PurePath(d.get("title") or "").stem)]
        if explicit_extension:
            extensions = set(re.findall(r"\.(pdf|docx?|pptx?|txt|md|ipynb)\b", question.casefold()))
            candidates = [d for d in candidates if PurePath(d.get("title") or "").suffix.casefold().lstrip(".") in extensions]
        if not candidates:
            assignments = [d for d in documents if d.get("source_type") == "assignment"
                           and family in numbered_references(d.get("title") or "")]
            if len(assignments) == 1:
                return {**result, "status": "matched", "supporting_document_keys": [assignments[0]["document_key"]]}
            return {**result, "status": "ambiguous" if assignments else "unresolved",
                    "candidates": [d.get("title") for d in assignments]}
        query_words = set(name_tokens(question))
        scored = [(len(set(_qualifiers(d, family)) & query_words), d) for d in candidates]
        best = max(score for score, _ in scored)
        candidates = [d for score, d in scored if score == best]
        groups = {_qualifiers(d, family) for d in candidates}
        if len(groups) > 1:
            return {**result, "status": "ambiguous", "candidates": sorted({d["title"] for d in candidates})}
        preferred = [d for d in candidates if document_role(d) == role]
        if role == "questions" and not preferred:
            preferred = [d for d in candidates if document_role(d) == "document"]
        if not preferred:
            return {**result, "status": "unresolved"}
        selected = preferred
    elif matches:
        best = max(score for score, _ in matches)
        selected = [d for score, d in matches if score == best]
    elif active_documents:
        active = [d for d in files if d["document_key"] in active_documents]
        if solution_intent(question):
            catalog = module_catalog(documents, modules)
            mids = {mid for doc in active for mid in _module_ids(doc)}
            module_families = {f for mid, record in catalog.items() if mid in mids for f in numbered_references(record['title'])}
            if len(mids) == 1 and len(module_families) == 1:
                kind, number = next(iter(module_families))
                return resolve_document_reference(f'solution for {kind} {number}', documents, modules=modules)
            families = {f for doc in active for f in numbered_references(doc.get("title") or "")}
            if len(families) == 1:
                kind, number = next(iter(families))
                return resolve_document_reference(f"solution for {kind} {number}", documents, modules=modules)
        selected = active
        if not selected:
            return result
    else:
        return result
    families = {f for doc in selected for f in numbered_references(doc.get("title") or "")}
    family = next(iter(families)) if len(families) == 1 else None
    return {**result, "status": "matched", "document_keys": sorted({d["document_key"] for d in selected}),
            "role": "solution" if all(document_role(d) == "solution" for d in selected) else "questions",
            "family": list(family) if family else None,
            "supporting_document_keys": _supporting_assignments(selected, documents, family, modules)}


def contextual_followup(question, active_documents):
    """Recognize bounded part references or actual code identifiers in active text."""
    if not active_documents or numbered_references(question):
        return False
    if re.search(r"\b(part\s+[a-z]|the solution|its solution)\b", question, re.I):
        return True
    identifiers = re.findall(r"\b\w*[a-z][A-Z]\w*\b", question)
    return any(re.search(r"\b" + re.escape(name) + r"\b", doc.get("text") or "")
               for doc in active_documents for name in identifiers)


def resolve_assignment_reference(reference, assignments, active_ids=()):
    """Score actual native assignment titles; preserve every distinguishing number.

    Alias tokens are compared within a reference, not replaced in arbitrary text.
    Empty references may use one active assignment; an explicit missing name
    never falls back to whichever assignment was previously discussed.
    """
    assignments = list(assignments)
    if not reference.strip():
        candidates = [record for record in assignments if record.get("canvas_id") in active_ids]
    else:
        words = _canonical_name(reference)
        query = " " + " ".join(words) + " "
        numbers = {word.lstrip("0") or "0" for word in words if word.isascii() and word.isdigit()}
        scored = []
        for record in assignments:
            title = _canonical_name(record.get("title") or "")
            title_numbers = {word.lstrip("0") or "0" for word in title if word.isascii() and word.isdigit()}
            if numbers and numbers != title_numbers:
                continue
            text = " " + " ".join(title) + " "
            if text.strip() and text in query:
                scored.append((100 + len(title), record))
            elif query.strip() and query in text:
                scored.append((70 + len(words), record))
        best = max((score for score, _ in scored), default=None)
        candidates = [record for score, record in scored if score == best]
    candidates = list({record["canvas_id"]: record for record in candidates}.values())
    return {"status": "matched" if len(candidates) == 1 else "ambiguous" if candidates else "missing",
            "assignment": candidates[0] if len(candidates) == 1 else None,
            "candidate_ids": [record["canvas_id"] for record in candidates],
            "candidates": [record.get("title") for record in candidates]}
