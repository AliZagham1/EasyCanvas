"""Bounded ordered context selection; no embeddings, provider calls or storage."""

import heapq
import hashlib
import re
from pathlib import PurePath

from backend.app.document_quality_service import looks_like_continuation
from backend.app.document_reference_service import name_tokens, REFERENCE_ALIASES

OVERVIEW_PASSAGES = 24
OVERVIEW_CHARACTERS = 32000


def select_numbered_section(question, items, *, max_passages=6, max_characters=12000):
    """Choose a bounded, ordered section from one indexed document when identifiable."""
    requests = re.findall(r'\b(question|problem|section|requirement|part)\s*#?\s*(\d+|[a-z])\b', question, re.I)
    if not requests:
        return []
    # A part is often inside a numbered question; anchor on the larger section.
    kind, number = min(requests, key=lambda pair: ('question', 'problem', 'section', 'requirement', 'part').index(pair[0].casefold()))
    kind, number = kind.casefold(), number.casefold()
    heading = re.compile(r'(?im)^\s*(?:#{1,6}\s*)?(question|problem|section|requirement|part)\s*#?\s*(\d+|[a-z])(?=\s|[.):—–-]|$)')
    groups = {}
    for item in items:
        for origin in item['sources']:
            if origin.get('source_type') == 'assignment':
                continue
            key = origin.get('document_key')
            index = origin.get('chunk_index')
            if key is not None and type(index) is int:
                groups.setdefault(key, {})[index] = item
    candidates = []
    for key, positions in groups.items():
        for index, item in positions.items():
            headings = list(heading.finditer(item['text']))
            for match in headings:
                if match[1].casefold() != kind or match[2].casefold() != number:
                    continue
                later = next((h.start() for h in headings if h.start() > match.start()), len(item['text']))
                body = item['text'][match.end():later].strip(' .:-—–\n')
                # A contents page commonly has several consecutive headings;
                # a real section normally has explanatory material below it.
                candidates.append(((len(body) >= 80, len(headings) == 1, min(len(body), 1000)), key, index, match.start(), match.end()))
    if not candidates:
        return []
    _, key, start, start_offset, offset = max(candidates, key=lambda value: value[0])
    positions = groups[key]
    peers = {'question': {'question', 'problem'}, 'problem': {'question', 'problem'},
             'section': {'section'}, 'requirement': {'requirement'}, 'part': {'part'}}[kind]
    selected, size = [], 0
    for index in range(start, start + max_passages):
        item = positions.get(index)
        if item is None or size + len(item['text']) > max_characters:
            break
        begin = offset if index == start else 0
        next_heading = next((h for h in heading.finditer(item['text'], begin)
                             if h[1].casefold() in peers), None)
        if next_heading and not item['text'][:next_heading.start()].strip():
            break
        beginning = start_offset if index == start else 0
        end = next_heading.start() if next_heading else len(item['text'])
        excerpt = item['text'][beginning:end].strip()
        if not excerpt:
            break
        entry = {**item, 'score': None, 'selection': 'numbered_section'}
        if excerpt != item['text']:
            entry.update(text=excerpt, source_text_hash=item['text_hash'],
                         text_hash=hashlib.sha256(excerpt.encode('utf-8')).hexdigest())
        selected.append(entry)
        size += len(excerpt)
        if next_heading:
            break
    return selected


def overview_requested(question, resolution, documents):
    """Distinguish explaining a resolved document from explaining a specific part."""
    keys = set(resolution["document_keys"] + resolution["supporting_document_keys"])
    if not keys:
        return False
    names = [name_tokens(PurePath(doc.get("title") or "").stem)
             for doc in documents if doc["document_key"] in keys]
    query = " " + " ".join(name_tokens(question)) + " "
    for tokens in sorted(names, key=len, reverse=True):
        if tokens:
            query = query.replace(" " + " ".join(tokens) + " ", " ")
    if re.search(r"\b(question|exercise|problem|part|page|slide)\s+(?:\d+|[a-z]\b|first|second|third)\b", query):
        return False
    if re.search(r"\b(all|every|entire|whole|overview|outline|summarize|summarise|list)\b", query):
        return True
    if re.search(r"\bwhat (?:is|s) in\b|\bwhat does\b.*\bask\b", query):
        return True
    if not re.search(r"\bexplain\b", query):
        return False
    canonical = lambda token: REFERENCE_ALIASES.get(token, token)
    title_words = {canonical(token) for tokens in names for token in tokens}
    noise = {"can", "could", "would", "you", "please", "me", "explain", "this", "that", "these", "the",
             "a", "an", "pdf", "document", "file", "which", "are", "in", "is", "it", "to", "do", "for", "of", "about"}
    remaining = {canonical(token) for token in name_tokens(query)} - title_words - noise
    return not remaining


def evidence_order(item):
    """Keep assignment instructions first and each document's chunks in order."""
    return min((0 if origin.get("source_type") == "assignment" else 1,
                origin["document_key"], origin["chunk_index"]) for origin in item["sources"])


def continuation_neighbors(items):
    """Link only consecutive chunks in the same document and adjacent source units."""
    by_position = {(origin["document_key"], origin["chunk_index"]): (item, origin)
                   for item in items for origin in item["sources"]}
    links = {item["text_hash"]: set() for item in items}
    for (key, index), (left, left_origin) in by_position.items():
        following = by_position.get((key, index + 1))
        if not following:
            continue
        right, right_origin = following
        linked = False
        for a in left_origin.get("locations") or []:
            for b in right_origin.get("locations") or []:
                if a.get("kind") != b.get("kind"):
                    continue
                number_key = next((field for field in ("page_number", "rendered_page_number", "slide_number", "cell_number")
                                   if field in a and field in b), None)
                same_unit = number_key is None or a[number_key] == b[number_key]
                # Overlap/split offsets prove these chunks share a source unit.
                if (same_unit and a.get("start_character", 0) < b.get("start_character", 0)
                        <= a.get("end_character", -1)):
                    linked = True
                elif (number_key and b[number_key] == a[number_key] + 1
                      and (a.get("continues_on") == b[number_key]
                           or b.get("continues_from") == a[number_key]
                           or looks_like_continuation(left["text"], right["text"]))):
                    linked = True
        if linked and left["text_hash"] != right["text_hash"]:
            links[left["text_hash"]].add(right["text_hash"])
            links[right["text_hash"]].add(left["text_hash"])
    return links


def continuation_bundle(item, available, links):
    """At most one adjacent chunk in each direction; keep source order."""
    by_hash = available if isinstance(available, dict) else {entry["text_hash"]: entry for entry in available}
    neighbors = [by_hash[key] for key in links.get(item["text_hash"], ())]
    # Duplicate provenance can add several links; preserve a bounded expansion.
    neighbors = sorted(neighbors, key=evidence_order)[:2]
    return sorted([item] + [{**entry, "score": None, "selection": "continuation"} for entry in neighbors], key=evidence_order)


def _spread_positions(count):
    """Cover the beginning, end, middle, then largest gaps, rather than a prefix."""
    if not count:
        return
    yield 0
    if count == 1:
        return
    yield count - 1
    gaps = [(-(count - 1), 0, count - 1)]
    while gaps:
        _, left, right = heapq.heappop(gaps)
        middle = (left + right) // 2
        if middle in (left, right):
            continue
        yield middle
        heapq.heappush(gaps, (-(middle - left), left, middle))
        heapq.heappush(gaps, (-(right - middle), middle, right))


def select_overview(items, *, max_passages=OVERVIEW_PASSAGES, max_characters=OVERVIEW_CHARACTERS):
    """Use all indexed material when it fits; otherwise take ordered broad coverage."""
    ordered = sorted(items, key=evidence_order)
    links = continuation_neighbors(ordered)
    by_hash = {entry["text_hash"]: entry for entry in ordered}
    selected, hashes, characters = [], set(), 0
    # Include linked instructions early, then distribute capacity across the file.
    instructions = [i for i, item in enumerate(ordered)
                    if any(origin.get("source_type") == "assignment" for origin in item["sources"])][:2]
    positions = instructions + list(_spread_positions(len(ordered)))
    for position in positions:
        item = {**ordered[position], "score": None, "selection": "document_order"}
        bundle = [entry for entry in continuation_bundle(item, by_hash, links) if entry["text_hash"] not in hashes]
        size = sum(len(entry["text"]) for entry in bundle)
        if len(selected) + len(bundle) > max_passages or characters + size > max_characters:
            continue
        for entry in bundle:
            selected.append(entry)
            hashes.add(entry["text_hash"])
        characters += size
    return sorted(selected, key=evidence_order)
