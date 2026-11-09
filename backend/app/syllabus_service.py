"""Ingest only API-linked UTA syllabus documents, without forwarding credentials."""

import hashlib
from urllib.parse import unquote, urlparse

import requests

from backend.app import document_service
from backend.app.content_discovery import build_syllabus_inventory, valid_syllabus_url

MAX_BYTES = 50 * 1024 * 1024


def fetch_syllabus(url, course_id, validators=None):
    """Return bounded document bytes/validators, or None bytes for HTTP 304.

    A fresh session ignores environment credentials/proxies and never receives
    Canvas headers. Redirects are rejected, so the allowlist cannot be bypassed.
    """
    if not valid_syllabus_url(url, course_id):
        raise ValueError("Unsupported syllabus document URL.")
    headers = {}
    if validators:
        if validators.get("etag"):
            headers["If-None-Match"] = validators["etag"]
        if validators.get("last_modified"):
            headers["If-Modified-Since"] = validators["last_modified"]
    with requests.Session() as session:
        session.trust_env = False
        with session.get(url, headers=headers, timeout=(10, 60), stream=True, allow_redirects=False) as response:
            if response.status_code == 304:
                if not headers:
                    raise ValueError("Unexpected syllabus cache response.")
                return None, validators
            response.raise_for_status()
            if response.status_code != 200:
                raise ValueError("Unsupported syllabus response.")
            if int(response.headers.get("Content-Length", 0)) > MAX_BYTES:
                raise ValueError("Syllabus exceeds the download limit.")
            content = bytearray()
            for part in response.iter_content(64 * 1024):
                if len(content) + len(part) > MAX_BYTES:
                    raise ValueError("Syllabus exceeds the download limit.")
                content.extend(part)
            filename = unquote(urlparse(url).path).rsplit("/", 1)[-1]
            # Reject HTML login/error pages masquerading as direct documents.
            if not (content.startswith(b"%PDF-") if filename.lower().endswith(".pdf") else content.startswith(b"PK\x03\x04")):
                raise ValueError("Syllabus response is not the expected document format.")
            return bytes(content), {"etag": response.headers.get("ETag"), "last_modified": response.headers.get("Last-Modified")}


def ingest_syllabus_documents(snapshot, store, *, force=False, max_files=None):
    """Yield document/action/byte-count results into the existing ingestion flow.

    URL aliases retain provenance without creating a second readable/chunked
    copy when the same bytes already exist as a current document in this course.
    """
    course_id = snapshot["course_id"]
    for index, entry in enumerate(build_syllabus_inventory(snapshot)):
        url = entry["url"]
        filename = unquote(urlparse(url).path).rsplit("/", 1)[-1]
        metadata = {"filename": filename}
        pipeline = document_service.file_pipeline_version(metadata)
        key = "syllabus_file:" + hashlib.sha256(url.encode()).hexdigest()
        document = {"document_key": key, "course_id": course_id, "canvas_id": None,
                    "source_type": "syllabus_file", "source_role": "course_syllabus",
                    "title": filename, "url": url, "references": entry["references"],
                    "pipeline_version": pipeline, "version": None}
        downloaded = 0
        if max_files is not None and index >= max_files:
            yield {**document, "status": "deferred", "text": ""}, "deferred", downloaded
            continue
        try:
            previous = store.load_document(course_id, key)
            current = store.load_documents(course_id, readable_only=True)
            by_key = {doc["document_key"]: doc for doc in current}
            cached = previous
            if previous and previous.get("canonical_document_key"):
                cached = by_key.get(previous["canonical_document_key"])
                if cached and cached.get("content_hash") != previous.get("content_hash"):
                    cached = None
            reusable = cached and not cached.get("stale") and cached.get("text") and cached.get("status") in ("ok", "partial") and cached.get("pipeline_version") == pipeline
            validators = previous.get("http_validators") if previous and reusable and not force else None
            content, validators = fetch_syllabus(url, course_id, validators)
            if content is None and not reusable:
                raise ValueError("Syllabus cache has no readable current document.")
            if content is not None:
                downloaded = len(content)
            content_hash = hashlib.sha256(content).hexdigest() if content is not None else previous["content_hash"]
            document.update(content_hash=content_hash, version=content_hash, http_validators=validators)
            # Do not merge across courses/accounts. A distinct URL needs its first
            # download before byte equality can safely establish an alias.
            match = next((doc for doc in current if doc["document_key"] != key
                          and doc.get("content_hash") == content_hash and doc.get("pipeline_version") == pipeline), None)
            if match:
                refs = list(match.get("references") or [])
                refs.extend(ref for ref in entry["references"] if ref not in refs)
                target = {name: value for name, value in match.items() if name not in ("stale", "latest_attempt")}
                target.update(references=refs, source_role="course_syllabus")
                store.save_document(course_id, target, snapshot["synced_at"])
                document.update(status="ok", text="", canonical_document_key=match["document_key"])
                action = "reused"
            elif reusable and cached.get("content_hash") == content_hash and not force:
                document = {**{name: value for name, value in cached.items() if name not in ("stale", "latest_attempt", "canonical_document_key")}, **document}
                action = "reused"
            else:
                document.update(document_service.extract_file(metadata, content))
                action = "extracted"
        except (requests.RequestException, ValueError) as error:
            status = error.response.status_code if isinstance(error, requests.RequestException) and error.response is not None else None
            document.update(status="unavailable" if status in (403, 404) else "error", http_status=status,
                            error_type=type(error).__name__, text="", pages=[])
            action = "failed"
        yield document, action, downloaded
