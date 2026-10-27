"""Find unique Canvas file references in API records without fetching URLs."""

import re
from html.parser import HTMLParser
from urllib.parse import unquote, urljoin, urlparse

from backend.app import canvas_service

HTML_FIELDS = ("body", "description", "message", "syllabus_body")
FILE_PATH = re.compile(r"^/(?:api/v1/)?(?:courses/\d+/)?files/(\d+)(?:/|$)")


class FileLinkParser(HTMLParser):
    """Collect URL attributes from HTML already returned by Canvas."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links = []

    def handle_starttag(self, tag, attrs):
        for key, value in attrs:
            if key in ("href", "src", "data", "data-api-endpoint") and value:
                self.links.append(value)


def course_records(snapshot):
    """Yield normalized records from each discovered course-content source."""
    if snapshot.get("syllabus"):
        yield snapshot["syllabus"]
    for source in ("assignments", "pages", "quizzes", "announcements", "discussions", "module_items", "files"):
        yield from snapshot.get(source, [])


def record_html(record):
    metadata = record.get("metadata") or {}
    return [metadata[field] for field in HTML_FIELDS if isinstance(metadata.get(field), str)]


def _positive_id(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, int) or isinstance(value, str) and value.isdigit():
        number = int(value)
        return number if number > 0 else None
    return None


def build_file_inventory(snapshot, canvas_url=None):
    """Group file IDs by identity and retain every source that referenced them.

    Only native file URLs on the configured Canvas origin are recognized.
    URLs are inspected locally; external tools and linked sites are never fetched.
    """
    course_id = snapshot["course_id"]
    base_url = (canvas_url or canvas_service.CANVAS_URL or "").rstrip("/")
    base = urlparse(base_url)
    inventory = {}

    def add(file_id, record, metadata=None):
        file_id = _positive_id(file_id)
        if file_id is None:
            return
        entry = inventory.setdefault(file_id, {
            "course_id": course_id,
            "file_id": file_id,
            "title": None,
            "metadata": None,
            "references": [],
        })
        reference = {key: record.get(key) for key in (
            "source_type", "canvas_id", "title", "url", "module_id", "module_name"
        )}
        if reference not in entry["references"]:
            entry["references"].append(reference)
        if metadata:
            entry["metadata"] = metadata
            entry["title"] = metadata.get("display_name") or metadata.get("filename") or record.get("title")

    for record in course_records(snapshot):
        source = record.get("source_type")
        metadata = record.get("metadata") or {}
        if source == "file":
            add(record.get("canvas_id"), record, metadata)
        elif source == "module_item" and record.get("item_type") == "File":
            add(record.get("content_id"), record)

        parser = FileLinkParser()
        for html in record_html(record):
            parser.feed(html)
        urls = parser.links
        for attachment in metadata.get("attachments") or []:
            if isinstance(attachment, dict):
                if attachment.get("id"):
                    add(attachment["id"], record, attachment)
                elif isinstance(attachment.get("url"), str):
                    urls.append(attachment["url"])
        if source == "module_item":
            urls += [value for value in (record.get("url"), metadata.get("external_url")) if isinstance(value, str)]
        for value in urls:
            try:
                parsed = urlparse(urljoin(base_url + "/", value))
            except ValueError:
                continue  # One malformed course link must not stop discovery.
            if not base.netloc or parsed.scheme not in ("http", "https"):
                continue
            if (parsed.scheme, parsed.netloc) != (base.scheme, base.netloc):
                continue
            match = FILE_PATH.search(unquote(parsed.path))
            if match:
                add(match[1], record)

    return list(inventory.values())
