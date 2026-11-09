"""Inspect PDF objects for unread visual content; never call an AI provider."""

import hashlib
import io
import math
from collections import defaultdict
from contextlib import closing
from dataclasses import dataclass

import pypdfium2 as pdfium
import pypdfium2.raw as pdfium_raw

INSPECTOR_VERSION = "page-inspection-v1"


@dataclass(frozen=True)
class InspectionSettings:
    """Conservative thresholds, kept together so changes are reviewable/versioned."""

    minimum_text: int = 200
    substantial_image_ratio: float = .015
    scanned_image_ratio: float = .65
    decorative_image_ratio: float = .005
    decorative_repetitions: int = 3
    drawing_count: int = 3
    drawing_area_ratio: float = .04
    math_symbols: int = 3
    max_objects: int = 20000
    render_scale: float = 2.5  # 180 DPI for standard PDF coordinates.
    max_render_side: int = 3200
    max_render_pixels: int = 10000000


@dataclass
class PageInspection:
    needs_vision: bool
    reasons: list[str]
    native_text_length: int
    image_count: int
    image_area_ratio: float
    scanned_page: bool
    drawing_count: int
    drawing_area_ratio: float
    image_bounds: list
    blank_page: bool
    decorative_images: int = 0


def _area(box):
    return max(0, box[2] - box[0]) * max(0, box[3] - box[1])


def _coverage(boxes, width, height):
    """Measure rectangle union, rather than double-count overlapping images."""
    if not boxes or width <= 0 or height <= 0:
        return 0.0
    # A cap keeps adversarial PDFs from causing an unbounded quadratic sweep.
    if len(boxes) > 200:
        return min(1.0, sum(map(_area, boxes)) / (width * height))
    xs = sorted({x for box in boxes for x in (box[0], box[2])})
    area = 0.0
    for left, right in zip(xs, xs[1:]):
        intervals = sorted((b[1], b[3]) for b in boxes if b[0] < right and b[2] > left)
        top, covered = -math.inf, 0.0
        for bottom, end in intervals:
            covered += max(0, end - max(bottom, top))
            top = max(top, end)
        area += (right - left) * covered
    return min(1.0, area / (width * height))


def _bounds(obj, width, height):
    box = obj.get_bounds()
    parent = obj.container
    # Form XObjects have their own coordinate systems.
    while parent is not None:
        box = parent.get_matrix().on_rect(*box)
        parent = parent.container
    box = (max(0, box[0]), max(0, box[1]), min(width, box[2]), min(height, box[3]))
    if not all(math.isfinite(v) for v in box):
        raise ValueError("Invalid PDF object bounds")
    return box


def inspect_pdf(pdf, native_pages, settings=None):
    """Return one inspection per page, including mixed pages with lots of text.

    Only tiny graphics repeated in a margin on several text-rich pages qualify
    as decoration. Unknown images and uncertain object inspection favor Vision.
    """
    settings = settings or InspectionSettings()
    records, occurrences = [], defaultdict(set)
    for number in range(len(pdf)):
        images, drawings, issues = [], [], []
        with closing(pdf[number]) as page:
            width, height = page.get_size()
            try:
                for index, obj in enumerate(page.get_objects()):
                    if index >= settings.max_objects:
                        issues.append("object_inspection_limit")
                        break
                    if obj.type == pdfium_raw.FPDF_PAGEOBJ_TEXT:
                        continue
                    box = _bounds(obj, width, height)
                    if obj.type == pdfium_raw.FPDF_PAGEOBJ_IMAGE:
                        ratio = _area(box) / max(1, width * height)
                        margin = box[3] <= height * .12 or box[1] >= height * .88
                        digest = None
                        if ratio <= settings.decorative_image_ratio and margin:
                            data = obj.get_data()
                            if len(data) <= 1000000:
                                digest = hashlib.sha256(bytes(data)).hexdigest()
                                occurrences[digest].add(number)
                        images.append({"bounds": box, "ratio": ratio, "margin": margin, "digest": digest})
                    elif obj.type in (pdfium_raw.FPDF_PAGEOBJ_PATH, pdfium_raw.FPDF_PAGEOBJ_SHADING):
                        # A lone full-page background is not an academic diagram.
                        background = (obj.type == pdfium_raw.FPDF_PAGEOBJ_PATH
                                      and _area(box) >= width * height * .98
                                      and pdfium_raw.FPDFPath_CountSegments(obj) <= 5)
                        if not background:
                            drawings.append(box)
                    elif obj.type == pdfium_raw.FPDF_PAGEOBJ_FORM and obj.level >= 13:
                        issues.append("deep_form_content")
            except (pdfium.PdfiumError, ValueError, OverflowError):
                issues.append("visual_inspection_incomplete")
            records.append((width, height, images, drawings, issues))

    inspections = []
    for number, (width, height, images, drawings, issues) in enumerate(records):
        native = native_pages[number].get("text", "") if number < len(native_pages) else ""
        meaningful = [image for image in images if not (
            image["digest"] and len(occurrences[image["digest"]]) >= settings.decorative_repetitions
            and len(native.strip()) >= settings.minimum_text)]
        ratio = _coverage([image["bounds"] for image in meaningful], width, height)
        drawing_ratio = _coverage(drawings, width, height)
        scanned = len(native.strip()) < settings.minimum_text and ratio >= settings.scanned_image_ratio
        reasons = list(issues)
        if scanned:
            reasons.append("scanned_or_image_dominant_page")
        if meaningful:
            reasons.append("substantial_image_content" if ratio >= settings.substantial_image_ratio else "unclassified_image_content")
            if len(native.strip()) < settings.minimum_text:
                reasons.append("little_text_with_images")
        if len(drawings) >= settings.drawing_count or drawing_ratio >= settings.drawing_area_ratio:
            reasons.append("unread_vector_content")
        if not native.strip() and drawings:
            reasons.append("drawing_only_page")
        if drawings and sum(c in "∑∫√∥⊥⋅⟂∈∉≈≠≤≥±" for c in native) >= settings.math_symbols:
            reasons.append("mathematical_layout_with_drawings")
        bad = sum(c == "\ufffd" or (ord(c) < 32 and c not in "\n\r\t") for c in native)
        if bad or (native.strip() and sum(c.isalnum() for c in native) / len(native) < .15):
            reasons.append("suspicious_native_text")
        blank = not native.strip() and not images and not drawings and not issues
        inspections.append(PageInspection(bool(reasons), sorted(set(reasons)), len(native),
                                          len(images), ratio, scanned, len(drawings), drawing_ratio,
                                          [list(i["bounds"]) for i in images], blank,
                                          len(images) - len(meaningful)))
    return inspections


def render_page(page, settings=None):
    """Render the whole page in memory with hard pixel limits; no files are saved."""
    settings = settings or InspectionSettings()
    width, height = page.get_size()
    if width <= 0 or height <= 0 or not math.isfinite(width * height):
        raise ValueError("Invalid page dimensions")
    scale = min(settings.render_scale, settings.max_render_side / max(width, height),
                math.sqrt(settings.max_render_pixels / (width * height)))
    with closing(page.render(scale=scale)) as bitmap:
        image = bitmap.to_pil()
        output = io.BytesIO()
        image.save(output, format="PNG")
    return output.getvalue()
