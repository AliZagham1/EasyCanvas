"""Split saved course text into traceable pieces; no downloads or AI calls."""

import argparse
import hashlib
import json
import re
from pathlib import PurePath

MAX_CHUNK_CHARACTERS = 2000
CHUNK_OVERLAP = 200
CHUNKER_VERSION = "course-chunks-v2:word-overlap:characters-2000:overlap-200"


def chunk_input_hash(document):
    """Fingerprint extracted text and locations, independently of Canvas timestamps."""
    payload = {key: document.get(key) for key in ("text", "pages", "slides", "blocks", "cells")}
    if document.get("rendered_pages"):
        payload["rendered_pages"] = document["rendered_pages"]
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _source_groups(document):
    """Keep pages/slides/cells separate; group small Word blocks by location."""
    for collection, kind in (("pages", "pdf_page"), ("slides", "slide"), ("rendered_pages", "rendered_word_page")):
        if document.get(collection):
            for unit in document[collection]:
                if unit.get("text", "").strip() and unit.get("status") in ("ok", "partial"):
                    # Raw native/visual text stays in extraction storage. Keep
                    # compact provenance in every chunk, without copying pages
                    # or complete model output into every location record.
                    excluded = {"text", "native_text", "visual_elements", "inspection"}
                    location = {key: value for key, value in unit.items() if key not in excluded}
                    if unit.get("visual_elements"):
                        location["element_types"] = sorted({element["type"] for element in unit["visual_elements"]})
                    yield [(unit["text"], {"kind": kind, **location}, 0)]
            return
    if document.get("blocks"):
        group, length, location = [], 0, None
        for block in document["blocks"]:
            text = block.get("text", "")
            if not text.strip() or block.get("status") not in ("ok", "partial"):
                continue
            if group and (block.get("location") != location or length + 2 + len(text) > MAX_CHUNK_CHARACTERS):
                yield group
                group, length = [], 0
            location = block.get("location")
            group.append((text, {"kind": "word_block", **{key: value for key, value in block.items() if key not in ("text", "kind")}, "block_kind": block.get("kind")}, 0))
            length += len(text) + (2 if len(group) > 1 else 0)
        if group:
            yield group
        return
    if document.get("cells"):
        for cell in document["cells"]:
            start, end = cell["start_character"], cell["end_character"]
            text = document["text"][start:end]
            if text.strip():
                yield [(text, {"kind": "notebook_cell", "cell_number": cell["cell_number"], "cell_type": cell["cell_type"], "cell_start_character": start, "cell_end_character": end}, start)]
        return
    if document.get("text", "").strip():
        yield [(document["text"], {"kind": "text"}, 0)]


def _text_spans(text):
    """Prefer paragraph/line/word boundaries, with bounded overlap and progress."""
    start = 0
    while start < len(text):
        end = min(start + MAX_CHUNK_CHARACTERS, len(text))
        if end < len(text):
            for separator in ("\n\n", "\n", " "):
                boundary = text.rfind(separator, start + MAX_CHUNK_CHARACTERS // 2, end)
                if boundary != -1:
                    end = boundary + len(separator)
                    break
        yield start, end
        if end == len(text):
            break
        next_start = max(start + 1, end - CHUNK_OVERLAP)
        # Move forward, never backward, so overlap cannot exceed 200 characters.
        # A huge unbroken word has no safe boundary: keep the existing fallback
        # rather than skipping unread source characters or making no progress.
        while next_start < end and _inside_word(text, next_start):
            next_start += 1
        start = next_start if not _inside_word(text, next_start) else max(start + 1, end - CHUNK_OVERLAP)


def _inside_word(text, position):
    """Treat letters, digits and underscores as a word, including Unicode."""
    return (0 < position < len(text)
            and (text[position - 1].isalnum() or text[position - 1] == "_")
            and (text[position].isalnum() or text[position] == "_"))


def source_quality(document):
    """Flag explicit instructor template instructions, never guess course policy."""
    flags = []
    if document.get("source_type") in ("syllabus", "page"):
        text = " ".join(document.get("text", "").casefold().split())
        if ("verify your syllabus" in text and "syllabus template" in text
                or "this page should remain unpublished" in text and "review the following" in text
                or "please review the content of this section before publishing this page" in text):
            flags.append("instructor_template_guidance")
    # True means no detected exclusion; it does not certify instructor review.
    return {"flags": flags, "policy_retrieval_eligible": not flags}


def chunk_metadata(document, chunk):
    """Derive explainable eligibility and exact-text reuse metadata without deletion.

    Ambiguous short headings/formulas stay eligible. Code cells are exempt from
    low-information rules. Metadata is recomputed on read so source-only changes
    do not require splitting text again.
    """
    text = chunk["text"]
    label = " ".join(text.split()).casefold()
    locations = chunk["locations"]
    code = (any(loc.get("cell_type") == "code" for loc in locations)
            or PurePath(document.get("title") or "").suffix.casefold()
            in (".c", ".h", ".cpp", ".hpp", ".py", ".java", ".js", ".ts", ".sh", ".sql", ".m"))
    reason = None
    if not code:
        page_or_slide = any(loc["kind"] in ("pdf_page", "slide", "rendered_word_page") for loc in locations)
        if page_or_slide and re.fullmatch(r"(?:page\s+)?\d{1,5}(?:\s*(?:/|of)\s*\d{1,5})?", label):
            reason = "page_number_only"
        elif re.fullmatch(r"[\w.()\-]+\.(?:pdf|docx?|pptx?|ipynb|txt|md|csv|zip|png|jpe?g)", label):
            reason = "filename_only"
        elif label.strip(" .:!#") in ("thank you", "the end", "end", "last", "about the course"):
            reason = "boilerplate_label_only"
        elif page_or_slide and len(label) <= 80:
            title = PurePath(document.get("title") or "").stem.casefold()
            units = document.get("pages") or document.get("slides") or document.get("rendered_pages") or []
            if label == title and sum(" ".join(unit.get("text", "").split()).casefold() == label for unit in units) >= 3:
                reason = "repeated_document_title_only"
    return {
        "text_hash": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "embedding_eligible": reason is None,
        "eligibility_reason": reason,
        "source_quality": source_quality(document),
    }


def chunk_document(document):
    """Return ordered text/location chunks from one readable extraction result.

    Offsets are end-exclusive within each page/slide/block, or within the full
    text for plain text and notebooks. Existing slide notes offsets are retained.
    """
    if document.get("stale") or document.get("status") not in ("ok", "partial") or not document.get("text", "").strip():
        return []
    chunks = []
    for group in _source_groups(document):
        parts, units, offset = [], [], 0
        for text, location, base in group:
            if parts:
                offset += 2
            units.append((offset, offset + len(text), location, base))
            parts.append(text)
            offset += len(text)
        text = "\n\n".join(parts)
        for start, end in _text_spans(text):
            if not text[start:end].strip():
                continue
            locations = [
                {**location, "start_character": base + max(start, unit_start) - unit_start,
                 "end_character": base + min(end, unit_end) - unit_start}
                for unit_start, unit_end, location, base in units
                if max(start, unit_start) < min(end, unit_end)
            ]
            chunks.append({"chunk_index": len(chunks), "text": text[start:end], "locations": locations})
    return chunks


def ensure_course_chunks(course_id, store):
    """Backfill or refresh chunks from SQLite; unchanged documents reuse their set."""
    if not isinstance(course_id, int) or isinstance(course_id, bool) or course_id <= 0:
        raise ValueError("course_id must be a positive integer.")
    report = {"processed": 0, "reused": 0, "chunks": 0}
    for document in store.load_documents(course_id, readable_only=True):
        fingerprint = chunk_input_hash(document)
        state = store.load_chunk_state(course_id, document["document_key"])
        if state and state["input_hash"] == fingerprint and state["chunker_version"] == CHUNKER_VERSION:
            report["reused"] += 1
            report["chunks"] += state["chunk_count"]
            continue
        chunks = chunk_document(document)
        store.save_chunks(course_id, document["document_key"], fingerprint, CHUNKER_VERSION, chunks)
        report["processed"] += 1
        report["chunks"] += len(chunks)
    return report


if __name__ == "__main__":
    from backend.app.canvas_service import get_cache_namespace
    from backend.app.ingestion_store import IngestionStore

    parser = argparse.ArgumentParser(description="Chunk an already-saved course without requesting Canvas data.")
    parser.add_argument("course_id", type=int)
    args = parser.parse_args()
    try:
        store = IngestionStore(get_cache_namespace())
        if store.load_course(args.course_id) is None:
            parser.exit(1, "No saved snapshot for this course/account. Ingest it first.\n")
        print(json.dumps(ensure_course_chunks(args.course_id, store)))
    except ValueError as error:
        parser.exit(1, f"Chunking failed: {error}\n")
