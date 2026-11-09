"""Interpret saved page boundaries without changing extracted text or calling AI."""

import copy
import re

UNREADABLE_MESSAGE = "Some content could not be read reliably."
OVERVIEW_MESSAGE = "This overview covers selected sections of the document."
COLLECTIONS = (("pages", "page_number"), ("rendered_pages", "rendered_page_number"), ("slides", "slide_number"))


def looks_like_continuation(left, right):
    """Require an unfinished ending and a plausible continuation, not a new section."""
    left, right = left.rstrip(), right.lstrip()
    if not left or not right or re.match(r"(?:#{1,6}\s|(?:question|exercise|section|chapter)\s+\d)", right, re.I):
        return False
    unfinished = (left[-1] in ":,;=+−-([{"
                  or bool(re.search(r"\b(is|are|the|a|an|of|and|to|with|by|for|called)\s*$", left, re.I))
                  or left[-1].isalnum() and right[0].islower())
    listed_continuation = (left.endswith(':') and re.match(r'\d+[.)]\s', right)
                           and re.search(r'\b(following|below|sequences|examples|cases|steps|items)\b', left[-250:], re.I))
    resumes = right[0].islower() or right.startswith(("\\", "$", "=", "+", "−", "[", "(")) or listed_continuation
    return bool(unfinished and resumes)


def _boundary_note(note, left, right):
    """Resolve only a quoted, end-of-page complaint with a readable successor.

    A blurry matrix, clipped figure, or missing value is not a page-continuation
    complaint. Unknown descriptions remain unreadable rather than being guessed.
    """
    if re.search(r"\b(unreadable|unclear|blurred|blurry|illegible|obscured|cropped|matrix entry|image|diagram|figure)\b", note, re.I):
        return False
    if re.search(r"\bpage break\b|\bcontinu(?:es|ation|ed)\b.{0,35}\bnext page\b", note, re.I):
        return looks_like_continuation(left, right)
    if not re.search(r"(?:page\s+ends\s+(?:after|at|with)|cut\s+off\s+after|mentioned after.{0,250}not visible on this page)", note, re.I):
        return False
    quotes = re.findall(r'''[“"‘']([^”"’']+)[”"’']''', note)
    def normalize(text):
        text = re.sub(r"\\(?:mathbf|mathrm|mathit|boldsymbol|text)\{([^{}]*)\}", r"\1", text)
        text = re.sub(r"\\[()[\]]|[{}*$]", "", text)
        return re.sub(r"\s+", " ", text).strip().casefold()
    end = normalize(left).rstrip(".:; ")
    anchored = any(end.endswith(normalize(value).rstrip(".:; ")) for value in quotes if normalize(value))
    return anchored and looks_like_continuation(left, right)


def assess_document_quality(document):
    """Return a copy with proven continuations resolved and true failures retained.

    Original transcriptions and raw Vision cache results are untouched. Resolved
    notes remain recorded for audit. Only continuation-only partial flags can be
    promoted; a different warning or failed page still makes the document partial.
    """
    result = copy.deepcopy(document)
    resolved_any = False
    active_units = []
    for collection, number_key in COLLECTIONS:
        units = result.get(collection) or []
        if not units:
            continue
        active_units = units
        ordered = sorted((unit for unit in units if type(unit.get(number_key)) is int), key=lambda u: u[number_key])
        for left, right in zip(ordered, ordered[1:]):
            if (right[number_key] != left[number_key] + 1
                    or left.get("status") not in ("ok", "partial")
                    or right.get("status") not in ("ok", "partial")
                    or not looks_like_continuation(left.get("text", ""), right.get("text", ""))):
                continue
            left["continues_on"] = right[number_key]
            right["continues_from"] = left[number_key]
            notes = left.get("unreadable") or []
            resolved = [note for note in notes if _boundary_note(note, left.get("text", ""), right.get("text", ""))]
            if not resolved:
                continue
            left["resolved_continuation_notes"] = list(dict.fromkeys((left.get("resolved_continuation_notes") or []) + resolved))
            left["unreadable"] = [note for note in notes if note not in resolved]
            if not left["unreadable"] and left.get("warnings") == ["visual_content_incomplete"]:
                left["warnings"] = []
                left["status"] = "ok"
                left["extraction_status"] = "vision_enriched" if left.get("extraction_method") == "vision" else "native_ok"
                resolved_any = True
        # The canonical collection wins, matching chunking/storage precedence.
        break
    if result.get("status") == "ok" and any(unit.get("status") in ("partial", "error", "no_text")
                                             or unit.get("unreadable") for unit in active_units):
        result["status"] = "partial"
    elif (resolved_any and result.get("status") == "partial" and not result.get("warnings")
            and all(unit.get("status") == "ok" for unit in active_units)):
        result["status"] = "ok"
    return result


def extraction_limitations(origin):
    """Keep extraction warnings distinct from index/context limits and page breaks."""
    locations = origin.get("locations") or []
    warnings = set(origin.get("warnings") or [])
    warnings.update(warning for location in locations for warning in location.get("warnings") or [])
    has_unreadable = any(location.get("unreadable") for location in locations)
    if origin.get("status") == "partial" or has_unreadable or warnings:
        # Structural uncertainty alone does not mean the text is unreadable.
        structural = {"legacy_doc_structure_unverified", "legacy_conversion_structure_unverified"}
        if has_unreadable or warnings - structural or origin.get("status") == "partial" and not warnings:
            warnings.add(UNREADABLE_MESSAGE)
    return sorted(warnings)


def answer_limitations(origin, question, *, overview=False):
    """Scope inherited document warnings to the cited units and requested content.

    Missing visuals elsewhere do not make clean Word blocks unreadable. Unit-local
    failures and unknown partial states remain warnings; visual-dependent questions
    and whole-document overviews retain document-level visual gaps conservatively.
    """
    locations = origin.get('locations') or []
    unit_warnings = {w for loc in locations for w in loc.get('warnings') or []}
    has_local_failure = any(loc.get('unreadable') or loc.get('status') in ('partial', 'error', 'no_text')
                            or loc.get('extraction_status') in ('vision_pending', 'vision_failed', 'partial', 'failed')
                            for loc in locations)
    visual = {'images_not_extracted', 'visual_content_not_extracted', 'drawing_or_embedded_content_not_extracted',
              'equations_not_extracted', 'embedded_content_not_extracted', 'visual_content_incomplete'}
    inherited = set(origin.get('warnings') or [])
    clear_units = bool(locations) and all(loc.get('status') == 'ok' for loc in locations)
    dependent = overview or bool(re.search(r'\b(image|diagram|figure|graph|screenshot|picture|table|matrix|matrices|equation|parse tree|syntax tree|AST)\b', question, re.I))
    if (not has_local_failure and not unit_warnings and clear_units and not inherited
            and origin.get('document_warning_scope') == 'units' and not dependent):
        return []
    if not has_local_failure and not unit_warnings and clear_units and inherited and inherited <= visual and not dependent:
        return []
    result = extraction_limitations(origin)
    if inherited & visual and dependent:
        result = [w for w in result if w not in visual]
        result.append('Some visual content relevant to this question has not been read reliably.')
    return sorted(set(result))


def document_warning_scope(document):
    """Distinguish known failures in other units from an unexplained partial file."""
    units = document.get('pages') or document.get('slides') or document.get('rendered_pages') or []
    if (not document.get('warnings') and any(u.get('status') in ('partial', 'error', 'no_text') or u.get('unreadable') for u in units)):
        return 'units'
    return 'document'


def reassess_saved_course(course_id, store):
    """Persist quality-only corrections and refresh metadata chunks, with no network."""
    from backend.app.chunking_service import ensure_course_chunks
    report = store.reassess_document_quality(course_id)
    return {**report, "chunking": ensure_course_chunks(course_id, store)}


if __name__ == "__main__":
    import argparse
    import json
    from backend.app.embedding_service import default_store
    parser = argparse.ArgumentParser(description="Reassess saved page continuations without downloading or using AI.")
    parser.add_argument("course_id", type=int, nargs="?")
    parser.add_argument("--all-current", action="store_true")
    args = parser.parse_args()
    if args.all_current == (args.course_id is not None):
        parser.error("Choose one course ID or --all-current")
    store = default_store()
    ids = store.saved_current_course_ids() if args.all_current else [args.course_id]
    print(json.dumps([{"course_id": cid, **reassess_saved_course(cid, store)} for cid in ids], indent=2))
