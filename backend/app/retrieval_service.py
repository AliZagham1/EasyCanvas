"""Course-scoped cosine search over saved embeddings; no answer generation."""

import argparse
import json
import math
import re

from backend.app.embedding_service import (
    EmbeddingConfiguration, EmbeddingProviderError, OpenAIEmbeddingProvider,
    default_store, validate_course_id,
)
from backend.app.ingestion_store import validate_vector
from backend.app.retrieval_context import (
    overview_requested, select_overview, continuation_neighbors, continuation_bundle, select_numbered_section,
)
from backend.app.document_reference_service import (
    name_tokens as _name_tokens, resolve_document_reference, module_catalog, document_module_names,
)


def cosine_similarity(left, right):
    """Compare vector direction; the score is similarity, not confidence."""
    validate_vector(left, len(left))
    validate_vector(right, len(left))
    left_norm, right_norm = math.hypot(*left), math.hypot(*right)
    score = math.fsum((a / left_norm) * (b / right_norm) for a, b in zip(left, right))
    return max(-1.0, min(1.0, score))


def resolve_documents(question, documents):
    """Compatibility helper returning the preferred file identities only."""
    return resolve_document_reference(question, documents)["document_keys"]


def _reference_failure(course_id, resolution):
    return {"course_id": course_id, "status": resolution["status"] + "_reference",
            "searchable_inputs": 0, "indexed_inputs": 0, "missing_inputs": 0,
            "question_prompt_tokens": 0, "results": [], "resolved_documents": [],
            "document_resolution": resolution}


def search_course(course_id, question, *, store=None, provider=None, configuration=None, top_k=5, document_keys=None):
    """Embed only the question and rank indexed current sources from one course.

    Template origins are removed before ranking, including within duplicate
    groups. Missing embeddings are reported, never silently generated here.
    """
    validate_course_id(course_id)
    if not isinstance(question, str) or not question.strip() or len(question.encode("utf-8")) > 8000:
        raise ValueError("Question must be nonblank and at most 8,000 UTF-8 bytes.")
    if type(top_k) is not int or not 1 <= top_k <= 50:
        raise ValueError("top_k must be between 1 and 50.")
    store = store or default_store()
    configuration = configuration or (provider.configuration if provider else EmbeddingConfiguration())
    if provider and provider.configuration != configuration:
        raise ValueError("Provider configuration does not match retrieval configuration.")
    snapshot = store.load_course(course_id)
    if snapshot is None:
        raise ValueError("No saved course snapshot for this account/course.")
    resolution = None
    documents = store.load_documents(course_id)
    modules = snapshot.get('modules') if isinstance(snapshot, dict) else None
    catalog = module_catalog(documents, modules)
    if document_keys is None:
        resolution = resolve_document_reference(question, documents, modules=modules)
        document_keys = resolution["document_keys"] + resolution["supporting_document_keys"]
        if resolution["status"] in ("ambiguous", "unresolved"):
            return _reference_failure(course_id, resolution)
    document_keys = set(document_keys or [])
    inputs = []
    for item in store.load_embedding_inputs(course_id):
        sources = [{**source, 'module_names': document_module_names(source, catalog)} for source in item["sources"] if source["source_quality"]["policy_retrieval_eligible"]
                   and (not document_keys or source["document_key"] in document_keys)]
        if sources:
            inputs.append({**item, "sources": sources})
    embeddings = store.load_embeddings(course_id, configuration)
    indexed = [item for item in inputs if item["text_hash"] in embeddings]
    report = {"course_id": course_id, "status": "ready", "searchable_inputs": len(inputs),
              "indexed_inputs": len(indexed), "missing_inputs": len(inputs) - len(indexed),
              "question_prompt_tokens": 0, "results": [], "resolved_documents": sorted(document_keys)}
    if resolution:
        report["document_resolution"] = resolution
    if not inputs:
        return {**report, "status": "no_content"}
    if not indexed:
        return {**report, "status": "not_indexed"}
    from backend.app.api_usage_service import UsageRecorder
    provider = provider or OpenAIEmbeddingProvider(configuration, usage_recorder=UsageRecorder(store))
    response = provider.embed_texts([question])
    if len(response["vectors"]) != 1:
        raise ValueError("Question provider returned the wrong vector count.")
    query_vector = response["vectors"][0]
    validate_vector(query_vector, configuration.dimensions)
    report["question_prompt_tokens"] = response.get("prompt_tokens")
    ranked = [{"score": cosine_similarity(query_vector, embeddings[item["text_hash"]]),
               "text": item["text"], "text_hash": item["text_hash"], "sources": item["sources"]}
              for item in indexed]
    ranked.sort(key=lambda item: (-item["score"], item["text_hash"]))
    report["results"] = ranked[:top_k]
    if report["missing_inputs"]:
        report["status"] = "partial"
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Search one already-indexed saved course; embeds only the question.")
    parser.add_argument("course_id", type=int)
    parser.add_argument("question")
    args = parser.parse_args()
    try:
        print(json.dumps(search_course(args.course_id, args.question), indent=2, ensure_ascii=False))
    except (ValueError, EmbeddingProviderError) as error:
        parser.exit(1, f"Search failed: {error}\n")


def retrieve_evidence(course_id, question, *, store=None, provider=None, focus_documents=(), document_keys=None, retrieval_mode=None):
    """Select diverse current evidence, then add bounded document/neighbor context.

    Uses one semantic search. Expansion reads only this course's current,
    indexed, policy-eligible chunks, never whole documents in the model prompt.
    """
    validate_course_id(course_id)
    if not isinstance(question, str) or not question.strip() or len(question.encode("utf-8")) > 8000:
        raise ValueError("Question must be nonblank and at most 8,000 UTF-8 bytes.")
    store = store or default_store()
    configuration = provider.configuration if provider else EmbeddingConfiguration()
    documents = store.load_documents(course_id)
    snapshot = store.load_course(course_id) or {}
    modules = snapshot.get('modules') if isinstance(snapshot, dict) else None
    catalog = module_catalog(documents, modules)
    resolution = resolve_document_reference(question, documents, focus_documents, modules=modules)
    if document_keys is not None:
        supplied = set(document_keys)
        resolution = {"status": "matched", "document_keys": [doc["document_key"] for doc in documents
                      if doc["document_key"] in supplied and doc.get("source_type") != "assignment"],
                      "supporting_document_keys": [doc["document_key"] for doc in documents
                      if doc["document_key"] in supplied and doc.get("source_type") == "assignment"],
                      "role": "questions", "family": None, "candidates": []}
        if not resolution["document_keys"] and not resolution["supporting_document_keys"]:
            return {"course_id": course_id, "status": "no_content", "results": [], "resolved_documents": []}
    if resolution["status"] in ("ambiguous", "unresolved"):
        if store.load_course(course_id) is None:
            raise ValueError("No saved course snapshot for this account/course.")
        return _reference_failure(course_id, resolution)
    current = {doc["document_key"] for doc in documents}
    scope = (resolution["document_keys"] + resolution["supporting_document_keys"]
             or [key for key in focus_documents if key in current])
    overview = (retrieval_mode == "overview" if retrieval_mode else overview_requested(question, resolution, documents))
    if overview:
        if store.load_course(course_id) is None:
            raise ValueError("No saved course snapshot for this account/course.")
        report = {"course_id": course_id, "question_prompt_tokens": 0, "results": []}
    else:
        options = {"document_keys": scope} if scope else {}
        report = search_course(course_id, question, store=store, provider=provider, top_k=20, **options)
    report.update(document_resolution=resolution, retrieval_mode="overview" if overview else "focused")
    if scope:
        report["resolved_documents"] = scope
    if not overview and not report["results"]:
        return report
    embeddings = store.load_embeddings(course_id, configuration)
    available, searchable = [], 0
    for item in store.load_embedding_inputs(course_id):
        origins = [{**origin, 'module_names': document_module_names(origin, catalog)} for origin in item["sources"] if origin["source_quality"]["policy_retrieval_eligible"]
                   and (not scope or origin["document_key"] in scope)]
        if origins:
            searchable += 1
            if item["text_hash"] in embeddings:
                available.append({**item, "sources": origins})
    if overview:
        missing = searchable - len(available)
        status = ("no_content" if not searchable else "not_indexed" if not available else "partial" if missing else "ready")
        selected = select_overview(available)
        return {**report, "status": status, "searchable_inputs": searchable, "indexed_inputs": len(available),
                "missing_inputs": missing, "results": selected,
                "evidence_coverage": {"selected": len(selected), "available": len(available)}}
    links = continuation_neighbors(available)
    by_hash = {item["text_hash"]: item for item in available}
    focus = set(scope or focus_documents)
    ignored = {"explain", "please", "what", "this", "that", "course", "assignment", "homework", "about", "need", "does", "have", "with", "which", "the", "and", "for"}
    words = {word for word in re.findall(r"\w+", question.casefold()) if (len(word) > 2 or word.isdigit()) and word not in ignored}
    for item in report["results"]:
        for origin in item["sources"]:
            title_words = set(re.findall(r"\w+", (origin.get("title") or "").casefold()))
            if len(words & title_words) >= 2:
                focus.add(origin["document_key"])
    # References relate native assignment instructions and attached documents.
    assignment_ids = set()
    for item in available:
        for origin in item["sources"]:
            if origin["document_key"] in focus:
                if origin.get("source_type") == "assignment":
                    assignment_ids.add(origin.get("canvas_id"))
                assignment_ids.update(ref.get("canvas_id") for ref in origin.get("references") or [] if ref.get("source_type") == "assignment")
    for item in available:
        for origin in item["sources"]:
            if (origin.get("source_type") == "assignment" and origin.get("canvas_id") in assignment_ids
                    or any(ref.get("source_type") == "assignment" and ref.get("canvas_id") in assignment_ids for ref in origin.get("references") or [])):
                focus.add(origin["document_key"])

    def focused(item):
        return any(origin["document_key"] in focus for origin in item["sources"])

    def shingles(text):
        tokens = re.findall(r"\w+", text.casefold())
        return {tuple(tokens[i:i + 5]) for i in range(max(1, len(tokens) - 4))}

    # Focused requests retain the smaller budget; required continuations
    # reserve space before unrelated semantic candidates fill that budget.
    max_passages, max_characters = 8, 16000
    selected, signatures, total = [], [], 0
    def add(item, *, bundle_neighbors=True):
        nonlocal total
        if any(item["text_hash"] == old["text_hash"] for old in selected):
            return False
        signature = shingles(item["text"])
        if not scope and any(len(signature & other) / max(1, min(len(signature), len(other))) > .7 for other in signatures):
            return False
        already = {old["text_hash"] for old in selected}
        candidates = continuation_bundle(item, by_hash, links) if bundle_neighbors else [item]
        bundle = [entry for entry in candidates if entry["text_hash"] not in already]
        size = sum(len(entry["text"]) for entry in bundle)
        if len(selected) + len(bundle) > max_passages or total + size > max_characters:
            return False
        for entry in bundle:
            selected.append(entry)
            signatures.append(shingles(entry["text"]))
        total += size
        return True

    if scope:
        section = select_numbered_section(question, available)
        if section:
            for index, item in enumerate(section):
                if index and item['text_hash'] in links.get(section[index - 1]['text_hash'], set()):
                    item = {**item, 'selection': 'continuation'}
                if not add(item, bundle_neighbors=False):
                    break
            supporting = set(resolution['supporting_document_keys'])
            for item in available:
                if any(origin['document_key'] in supporting for origin in item['sources']):
                    add({**item, 'score': None, 'selection': 'assignment_instructions'})
            return {**report, 'results': selected}

    if scope:
        supporting = set(resolution["supporting_document_keys"])
        instruction_passages = 0
        for item in available:
            if instruction_passages < 2 and any(origin["document_key"] in supporting for origin in item["sources"]):
                if add({**item, "score": None, "selection": "assignment_instructions"}):
                    instruction_passages += 1


    if scope:
        # A question number is distinct from the numeric document suffix.
        number = re.search(r"\bquestion\s+(\d+)\b", question, re.I)
        if number:
            pattern = r"\bquestion\s*" + re.escape(number[1]) + r"\b"
            for item in available:
                if re.search(pattern, item["text"], re.I):
                    add({**item, "score": None, "selection": "question_number"})
        location = re.search(r"\b(page|slide)\s+(\d+)\b", question, re.I)
        if location:
            key = "slide_number" if location[1].casefold() == "slide" else "page_number"
            for item in available:
                if any(loc.get(key) == int(location[2]) or key == "page_number" and loc.get("rendered_page_number") == int(location[2])
                       for origin in item["sources"] for loc in origin["locations"]):
                    add({**item, "score": None, "selection": "source_location"})
        # File-title words can dominate a semantic query and hide a rare term
        # inside that very file. Add a few bounded lexical anchors from the
        # current indexed scope, weighting rare terms rather than raw counts.
        title_tokens = {token for doc in documents if doc["document_key"] in scope for token in _name_tokens(doc.get("title") or "")}
        stop = ignored | {"from", "say", "says", "show", "shows", "kind", "according", "many", "did", "how", "what", "are", "was", "were", "students", "student", "its", "the", "can", "you", "please"}
        terms = set(_name_tokens(question)) - title_tokens - stop
        vocabularies = [(item, set(_name_tokens(item["text"]))) for item in available]
        frequencies = {term: sum(term in tokens for _, tokens in vocabularies) for term in terms}
        lexical = []
        for item, tokens in vocabularies:
            matched = terms & tokens
            if len(matched) >= 2:
                weight = sum(math.log((len(available) + 1) / (frequencies[token] + .5)) for token in matched)
                lexical.append((weight, item))
        for _, item in sorted(lexical, key=lambda pair: -pair[0])[:3]:
            add({**item, "score": None, "selection": "document_terms"})

    def assignment_evidence(item):
        return focused(item) and any(origin.get("source_type") == "assignment" or any(
            ref.get("source_type") == "assignment" for ref in origin.get("references") or []) for origin in item["sources"])

    candidates = sorted(report["results"], key=lambda item: -(item["score"] + (.08 if focused(item) else 0) + (.12 if assignment_evidence(item) else 0)))
    for item in candidates:
        add(item)
        if len(selected) == 5:
            break
    primary = selected[0]["sources"][0] if selected else None
    related = [item for item in available if focused(item) or primary and any(origin["document_key"] == primary["document_key"] for origin in item["sources"])]
    scores = {item["text_hash"]: item["score"] for item in report["results"]}
    # Neighbors outside the semantic candidate pool have no measured score.
    expanded = [{**item, "score": scores.get(item["text_hash"]), "selection": "same_document"} for item in related]
    if re.search(r"\b(explain|overview|summari[sz]e)\b", question, re.I):
        for facet in (r"\b(submit|submission|deliverable)", r"\b(requirement|must|required|task|select|choose|answer|implement)", r"\b(rubric|grading|constraint)"):
            for item in expanded:
                if re.search(facet, item["text"], re.I) and add(item):
                    break
    anchors = [(origin["document_key"], origin["chunk_index"]) for item in selected for origin in item["sources"]]
    for item in expanded:
        if any(origin["document_key"] == key and abs(origin["chunk_index"] - number) == 1 for origin in item["sources"] for key, number in anchors):
            add({**item, "selection": "neighbor"})
    return {**report, "results": selected}
