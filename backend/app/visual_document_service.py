"""Enrich native file extraction at ingestion time, before existing chunking."""

import copy
import hashlib
import json
import os
import re
from contextlib import closing
from dataclasses import asdict

import pypdfium2 as pdfium

from backend.app import document_service
from backend.app.visual_inspection import INSPECTOR_VERSION, InspectionSettings, inspect_pdf, render_page
from backend.app.vision_service import (
    OpenAIVisionExtractionProvider, VisionConfiguration, VisionProviderError, validate_extraction,
)

VISUAL_VERSION = "visual-document-v1"
VISUAL_WARNINGS = {"images_not_extracted", "drawing_or_embedded_content_not_extracted", "equations_not_extracted",
                   "text_boxes_not_extracted", "content_controls_not_extracted", "footnotes_not_extracted",
                   "embedded_content_not_extracted"}
SUPPORTED = ("pdf", "doc", "docx", "ppt", "pptx")


def pending_visual_content(document):
    """Identify unprocessed/failed visuals; a genuine completed partial is retained."""
    if document.get('stale') or document.get('format') not in SUPPORTED:
        return False
    report = document.get('visual_processing') or {}
    if report.get('pending') or report.get('failed') or report.get('retry_needed'):
        return True
    units = document.get('pages') or document.get('slides') or document.get('rendered_pages') or document.get('visual_inspection_pages') or []
    if any(u.get('extraction_status') in ('vision_pending', 'vision_failed', 'rendered_content_missing') for u in units):
        return True
    return bool(set(document.get('warnings') or []) & (VISUAL_WARNINGS | {'visual_content_not_extracted', 'office_visual_rendering_unavailable'}))


def same_pipeline_except_enabled(left, right):
    """Toggling paid work alone must not invalidate complete, compatible extraction."""
    normalize = lambda value: (value or '').replace(':inspection-only:', ':visual-mode:').replace(':enabled:', ':visual-mode:')
    return bool(left and right and normalize(left) == normalize(right))


def _native_text_coverage(native, rendered):
    """Detect conversions that lost ordinary native words, without guessing math."""
    words = set(re.findall(r"[^\W\d_]{2,}", native.casefold()))
    if len(words) < 2:
        return 1.0
    target = set(re.findall(r"[^\W\d_]{2,}", rendered.casefold()))
    return len(words & target) / len(words)


def vision_enabled():
    """Paid enrichment is an explicit backend setting, never a query-time action."""
    return os.getenv("EASYCANVAS_VISION_ENABLED", "false").casefold() in ("1", "true", "yes")


def visual_configuration(provider=None, settings=None):
    config = provider.configuration if provider else VisionConfiguration(model=os.getenv("EASYCANVAS_VISION_MODEL", "gpt-6-luna"))
    settings = settings or InspectionSettings()
    rendering = {key: asdict(settings)[key] for key in ("render_scale", "max_render_side", "max_render_pixels")}
    payload = [VISUAL_VERSION, pdfium.PYPDFIUM_INFO.version, rendering, config.fingerprint]
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def file_pipeline_version(metadata, provider=None):
    """Version only the file-processing path; native/syllabus extraction stays intact."""
    native = document_service.file_pipeline_version(metadata)
    if document_service.file_format(metadata) not in SUPPORTED:
        return native
    enabled = provider is not None or vision_enabled()
    renderer = ""
    if document_service.file_format(metadata) != "pdf":
        from backend.app.legacy_office import converter_fingerprint
        renderer = f":office-render-v3:{converter_fingerprint()}"
    inspection = hashlib.sha256(json.dumps(asdict(InspectionSettings()), sort_keys=True).encode()).hexdigest()
    return f"{native}:{VISUAL_VERSION}:{INSPECTOR_VERSION}:{inspection}:{'enabled' if enabled else 'inspection-only'}:{visual_configuration(provider)}{renderer}"


def _normalize_page(unit, result, config_hash):
    """Use one complete-page transcription instead of duplicating native paragraphs."""
    extraction = validate_extraction(result["extraction"])
    text = "\n\n".join(element["text"].replace("\r\n", "\n").strip("\n")
                         if element["type"] == "code" else element["text"].strip()
                         for element in extraction["elements"])
    unit.update(visual_elements=extraction["elements"], visual_config_hash=config_hash,
                unreadable=extraction["unreadable"], extraction_method="vision")
    configuration = result.get("configuration") or {}
    unit.update(vision_model=configuration.get("model"), vision_provider=configuration.get("provider"),
                vision_prompt_version=configuration.get("prompt_version"))
    warnings = set(unit.get("warnings") or []) - VISUAL_WARNINGS
    if not extraction["complete"] or not text.strip():
        warnings.add("visual_content_incomplete")
    # Preserve native words if the provider returned only a caption/empty output.
    native = unit.get("native_text", "")
    if native.strip() and len(text.strip()) < min(100, len(native.strip()) // 3):
        warnings.add("native_content_not_preserved")
        text = native
        unit["extraction_method"] = "native"
    unit.update(text=text or native, warnings=sorted(warnings),
                status="partial" if warnings else "ok", extraction_status="vision_enriched" if not warnings else "partial")


def _process_pdf(content, native_pages, *, store, course_id, content_hash, unit_kind,
                 provider=None, enabled=False, settings=None, required_units=()):
    settings = settings or InspectionSettings()
    config_hash = visual_configuration(provider, settings)
    pages = copy.deepcopy(native_pages)
    report = {"inspected": 0, "required": 0, "generated": 0, "reused": 0,
              "pending": 0, "failed": 0, "input_tokens": 0, "output_tokens": 0}
    with pdfium.PdfDocument(content) as pdf:
        if len(pdf) != len(pages):
            raise ValueError("Rendered page count does not match source units")
        inspections = inspect_pdf(pdf, pages, settings)
        for index, (unit, inspection) in enumerate(zip(pages, inspections)):
            number = index + 1
            unit["native_text"] = unit.get("text", "")
            unit.update(inspection=asdict(inspection), extraction_method="native", extraction_status="native_ok")
            report["inspected"] += 1
            if number in required_units:
                inspection.needs_vision = True
                inspection.reasons = sorted(set(inspection.reasons) | {"native_reader_reports_unread_content"})
                unit["inspection"] = asdict(inspection)
            if inspection.blank_page:
                if number in required_units:
                    unit.update(status="partial" if unit.get("text") else "no_text", extraction_status="rendered_content_missing",
                                warnings=["native_visual_missing_from_render"])
                    report["required"] += 1
                    report["failed"] += 1
                    continue
                unit.update(text="", status="ok", extraction_status="blank")
                continue
            if not inspection.needs_vision:
                # A page containing no text cannot be called readable merely
                # because inspection failed to classify its native error.
                if unit.get("status") == "error":
                    unit["extraction_status"] = "failed"
                continue
            report["required"] += 1
            # Include reference text and rendered pixels: converter/font
            # changes must never silently reuse a different page's transcription.
            with closing(pdf[index]) as page:
                image = render_page(page, settings)
            page_config = hashlib.sha256(json.dumps([config_hash, hashlib.sha256(image).hexdigest(),
                                                     unit["native_text"]], sort_keys=True).encode()).hexdigest()
            cached = store.load_visual_extraction(course_id, content_hash, unit_kind, number, page_config)
            if cached:
                if not cached.get("configuration"):
                    cached["configuration"] = asdict(provider.configuration if provider else VisionConfiguration(model=os.getenv("EASYCANVAS_VISION_MODEL", "gpt-6-luna")))
                _normalize_page(unit, cached, page_config)
                report["reused"] += 1
                continue
            unit.update(status="partial" if unit.get("text") else "no_text", extraction_status="vision_pending",
                        warnings=sorted(set(unit.get("warnings") or []) | {"visual_content_not_extracted"}))
            if not enabled:
                report["pending"] += 1
                continue
            try:
                from backend.app.api_usage_service import UsageRecorder
                provider = provider or OpenAIVisionExtractionProvider(usage_recorder=UsageRecorder(store))
                result = provider.extract_page(image, native_text=unit["native_text"])
                validate_extraction(result["extraction"])
                result["configuration"] = asdict(provider.configuration)
                store.save_visual_extraction(course_id, content_hash, unit_kind, number, page_config, result)
                # The pending marker describes the attempt, not the saved result.
                unit["warnings"] = [w for w in unit["warnings"] if w != "visual_content_not_extracted"]
                _normalize_page(unit, result, page_config)
                report["generated"] += 1
                for key in ("input_tokens", "output_tokens"):
                    report[key] += (result.get("usage") or {}).get(key, 0) or 0
            except (VisionProviderError, pdfium.PdfiumError, ValueError, KeyError):
                unit["extraction_status"] = "vision_failed"
                unit["warnings"] = sorted(set(unit["warnings"]) | {"visual_extraction_failed"})
                report["failed"] += 1
    return pages, report


def extract_file(metadata, content, *, store, course_id, provider=None, enabled=None, settings=None):
    """Native reader → visual inspection → cached enrichment → existing document shape.

    Providers and settings are injectable for deterministic tests. No Canvas
    networking, chunk creation, embeddings or conversational logic live here.
    """
    native = document_service.extract_file(metadata, content)
    format_name = document_service.file_format(metadata)
    if format_name not in SUPPORTED or native["status"] not in ("ok", "partial", "no_text"):
        return native
    enabled = (provider is not None or vision_enabled()) if enabled is None else enabled
    result = copy.deepcopy(native)
    content_hash = hashlib.sha256(content).hexdigest()
    try:
        if format_name == "pdf":
            units, report = _process_pdf(content, native["pages"], store=store, course_id=course_id,
                                         content_hash=content_hash, unit_kind="pdf_page", provider=provider,
                                         enabled=enabled, settings=settings)
            result["pages"] = units
        elif format_name.startswith("ppt"):
            # Inspect even apparently successful native slides: text boxes can
            # hide unread connector relationships and other vector content.
            from backend.app.legacy_office import render_office_pdf
            rendered = render_office_pdf(content, format_name)
            slides = native["slides"]
            pdf_native = document_service.extract_pdf(rendered)
            if len(pdf_native["pages"]) != len(slides):
                raise ValueError("Rendered slide identity cannot be verified")
            required = {s["slide_number"] for s in slides if s.get("warnings")}
            units, report = _process_pdf(rendered, pdf_native["pages"], store=store, course_id=course_id,
                                         content_hash=content_hash, unit_kind="slide", provider=provider,
                                         enabled=enabled, settings=settings, required_units=required)
            for slide, unit in zip(slides, units):
                old = copy.deepcopy(slide)
                if not unit["inspection"]["needs_vision"]:
                    slide.update(inspection=unit["inspection"], extraction_method="native", extraction_status="native_ok")
                    if unit.get("extraction_status") == "blank":
                        slide.update(status="ok", extraction_status="blank")
                    continue
                if unit.get("extraction_method") != "vision":
                    # Inspection-only and failed enrichment must not rewrite a
                    # working native slide or discard its tables/speaker notes.
                    slide.update(inspection=unit["inspection"], extraction_method="native",
                                 extraction_status=unit["extraction_status"],
                                 warnings=sorted(set(old.get("warnings", [])) | set(unit.get("warnings", []))),
                                 status="partial" if old.get("text") else "no_text")
                    continue
                slide.update({k: v for k, v in unit.items() if k != "page_number"})
                slide["native_text"] = old["text"]
                notes = old["text"][old["notes_start_character"]:] if "notes_start_character" in old else ""
                if notes:
                    prefix = "\n\nSpeaker notes:\n"
                    slide["notes_start_character"] = len(slide["text"]) + len(prefix)
                    slide["text"] += prefix + notes
                    slide["extraction_method"] = "native+vision" if slide.get("extraction_method") == "vision" else "native"
                slide["warnings"] = sorted(set(slide.get("warnings", [])) | (set(old.get("warnings", [])) - VISUAL_WARNINGS))
                if slide["warnings"]:
                    slide["status"] = "partial" if slide["text"] else "no_text"
            result["slides"] = units = slides
        else:
            if format_name != "doc" and not set(native.get("warnings", [])) & VISUAL_WARNINGS:
                return native
            from backend.app.legacy_office import render_office_pdf
            rendered = render_office_pdf(content, format_name)
            pdf_native = document_service.extract_pdf(rendered)
            units, report = _process_pdf(rendered, pdf_native["pages"], store=store, course_id=course_id,
                                         content_hash=content_hash, unit_kind="rendered_word_page", provider=provider,
                                         enabled=enabled, settings=settings)
            # Keep original native blocks; never invent a mapping from Word
            # paragraph numbers to renderer-dependent pagination.
            for unit in units:
                unit["rendered_page_number"] = unit.pop("page_number")
                unit["location"] = f"Rendered Word page {unit['rendered_page_number']}"
            if not enabled and native.get("text") and not any(unit.get("extraction_method") == "vision" for unit in units):
                result["visual_inspection_pages"] = [{key: value for key, value in unit.items()
                                                       if key not in ("text", "native_text", "visual_elements")} for unit in units]
                result["visual_processing"] = {**report, "enabled": False, "retry_needed": False,
                                                "configuration": visual_configuration(provider, settings)}
                return result
            rendered_text = "\n\n".join(unit.get("text", "") for unit in units)
            if _native_text_coverage(native.get("text", ""), rendered_text) < .6:
                # LibreOffice may import some legacy bytes as gibberish or lose
                # Word content. Do not replace good native text with that output.
                result.update(status="partial" if native.get("text") else "no_text",
                              warnings=sorted(set(native.get("warnings", [])) | {"rendered_native_text_mismatch"}),
                              visual_processing={**report, "enabled": enabled, "retry_needed": False,
                                                 "error_type": "RenderedNativeTextMismatch"})
                return result
            result["rendered_pages"] = units
            result["warnings"] = sorted(set(native.get("warnings", [])) - VISUAL_WARNINGS)
        result["text"] = "\n\n".join(u["text"] for u in units if u.get("text"))
        result["character_count"] = len(result["text"])
        incomplete = any(u["status"] not in ("ok",) for u in units) or bool(result.get("warnings"))
        result["status"] = ("partial" if incomplete else "ok") if result["text"] else "no_text"
        result["visual_processing"] = {**report, "retry_needed": bool(report["failed"]), "enabled": enabled,
                                        "configuration": visual_configuration(provider, settings)}
    except (pdfium.PdfiumError, ValueError) as error:
        result.update(status="partial" if result.get("text") else "no_text",
                      warnings=sorted(set(result.get("warnings") or []) | {"visual_inspection_failed"}),
                      visual_processing={"retry_needed": True, "enabled": enabled, "error_type": type(error).__name__})
    except Exception as error:
        # Converters can be unavailable. Keep native text and safe categories;
        # never turn one failed visual document into a failed course sync.
        from backend.app.legacy_office import LegacyConversionError
        if not isinstance(error, LegacyConversionError):
            raise
        result.update(status="partial" if result.get("text") else "no_text",
                      warnings=sorted(set(result.get("warnings") or []) | {"office_visual_rendering_unavailable"}),
                      visual_processing={"retry_needed": True, "enabled": enabled, "error_type": error.error_type})
    from backend.app.document_quality_service import assess_document_quality
    return assess_document_quality(result)
