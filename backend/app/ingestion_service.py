"""Discover course content, or explicitly ingest supported documents into local storage."""

import argparse
import hashlib
import json
import time
from datetime import datetime, timezone

import requests

from backend.app import canvas_service
from backend.app.content_discovery import HTML_FIELDS, build_file_inventory, course_records, record_html


def _normalize(record, course_id, source_type, module=None):
    """Keep a common envelope and the original Canvas fields for future syncs."""
    return {
        "canvas_id": record.get("id", record.get("page_id")),
        "course_id": course_id,
        "source_type": source_type,
        "title": record.get("title") or record.get("display_name") or record.get("name"),
        # Pages use `url` for a slug; prefer their actual Canvas HTML URL.
        "url": record.get("html_url") or record.get("url"),
        "module_id": module.get("id") if module else None,
        "module_name": module.get("name") if module else None,
        "content_id": record.get("content_id"),
        "item_type": record.get("type"),
        "updated_at": record.get("updated_at"),
        "created_at": record.get("created_at"),
        "metadata": dict(record),
    }


def sync_course(course_id, *, include_page_bodies=False):
    """Return a discovery snapshot, including explicit status for each source.

    Course metadata must be accessible. Failures in other sources are recorded
    without pretending the source is empty or discarding successful discovery.
    Module references remain separate from course-wide content records.
    """
    details = canvas_service.get_course_details(course_id)
    result = {
        "course_id": course_id,
        "synced_at": datetime.now(timezone.utc).isoformat(),
        "course": _normalize(details, course_id, "course"),
        "syllabus": None,
        "source_status": {"course": {"status": "ok"}},
    }

    # Reuse the syllabus returned with course details; no second API request.
    body = details.get("syllabus_body")
    if body and body.strip():
        result["syllabus"] = {
            **_normalize(details, course_id, "syllabus"),
            "title": "Syllabus",
            "body": body,
        }
    result["source_status"]["syllabus"] = {
        "status": "ok" if "syllabus_body" in details else "unavailable"
    }

    def discover(source, fetch):
        try:
            records = fetch()
        except requests.RequestException as error:
            status_code = error.response.status_code if error.response is not None else None
            records = getattr(error, "records", [])
            result["source_status"][source] = {
                "status": "partial" if records else "unavailable" if status_code in (403, 404) else "error",
                "http_status": status_code,
                "error_type": type(error).__name__,
            }
            return records
        result["source_status"][source] = {"status": "ok"}
        return records

    for source, source_type, fetch in (
        ("assignments", "assignment", canvas_service.get_assignments),
        ("modules", "module", canvas_service.get_modules),
        ("pages", "page", lambda cid: canvas_service.get_pages(cid, include_body=True) if include_page_bodies else canvas_service.get_pages(cid)),
        ("files", "file", canvas_service.get_files),
        ("quizzes", "quiz", canvas_service.get_quizzes),
        ("announcements", "announcement", canvas_service.get_announcements),
        ("discussions", "discussion", canvas_service.get_discussions),
    ):
        records = discover(source, lambda fetch=fetch: fetch(course_id))
        result[source] = [_normalize(record, course_id, source_type) for record in records]

    result["module_items"] = []
    module_statuses = {}
    for module in result["modules"]:
        module_id = module["canvas_id"]
        source = f"module_items:{module_id}"
        items = discover(source, lambda: canvas_service.get_module_items(course_id, module_id))
        module_statuses[str(module_id)] = result["source_status"].pop(source)
        result["module_items"].extend(
            _normalize(item, course_id, "module_item", module["metadata"]) for item in items
        )

    result["source_status"]["module_items"] = {
        "status": (
            result["source_status"]["modules"]["status"] if result["source_status"]["modules"]["status"] != "ok"
            else "partial" if any(status["status"] != "ok" for status in module_statuses.values())
            else "ok"
        ),
        "modules": module_statuses,
    }
    if include_page_bodies:
        known = {page["metadata"].get("url"): page for page in result["pages"]}
        requested = {}
        for page in result["pages"]:
            if "body" not in page["metadata"]:
                locator = page["metadata"].get("url") or f"page_id:{page['canvas_id']}"
                requested[locator] = page
        for item in result["module_items"]:
            locator = item["metadata"].get("page_url")
            if item["item_type"] == "Page" and locator and locator not in known:
                requested.setdefault(locator, None)
        for locator, existing in requested.items():
            fetched = discover(f"page:{locator}", lambda locator=locator: [canvas_service.get_page(course_id, locator)])
            if fetched:
                normalized = _normalize(fetched[0], course_id, "page")
                if existing is not None:
                    existing.update(normalized)
                else:
                    result["pages"].append(normalized)
        body_status = "partial" if any("body" not in page["metadata"] for page in result["pages"]) or any(
            status["status"] != "ok" for source, status in result["source_status"].items() if source.startswith("page:")
        ) else "ok"
        result["source_status"]["page_bodies"] = {"status": body_status}
    result["file_inventory"] = build_file_inventory(result)
    return result


def format_sync_summary(result):
    course = result["course"]["metadata"]
    lines = [f"Course: {course.get('course_code') or course.get('name') or result['course_id']}"]
    status = result["source_status"]["syllabus"]["status"]
    syllabus = ("found" if result["syllabus"] else "not found") if status == "ok" else status
    lines.append(f"Syllabus: {syllabus}")
    for source, label in (
        ("assignments", "Assignments"), ("modules", "Modules"),
        ("module_items", "Module items"), ("pages", "Pages"),
        ("files", "Files"), ("quizzes", "Quizzes"),
        ("announcements", "Announcements"), ("discussions", "Discussions"),
    ):
        status = result["source_status"][source]["status"]
        count = str(len(result[source]))
        value = count if status == "ok" else f"{count} (partial)" if status == "partial" else f"{count} (listing {status})" if result[source] else status
        http_status = result["source_status"][source].get("http_status")
        if http_status is not None:
            value += f" (HTTP {http_status})"
        lines.append(f"{label}: {value}")
    return "\n".join(lines)


def _file_version(metadata):
    """Use remote timestamps for skipping downloads; IDs alone prove no freshness."""
    timestamp = metadata.get("updated_at") or metadata.get("modified_at")
    if not timestamp:
        return None
    fields = {key: metadata.get(key) for key in (
        "updated_at", "modified_at", "size", "filename", "display_name", "content-type", "content_type", "md5",
    )}
    return hashlib.sha256(json.dumps(fields, sort_keys=True).encode()).hexdigest()


def _cached_text(previous, document, force, *, content_hash=None):
    """Reuse an unchanged extraction, including known unsupported/image-only results."""
    if force or not previous:
        return None
    attempt = previous["latest_attempt"]
    same_version = document["version"] is not None and attempt.get("version") == document["version"]
    same_content = content_hash is not None and attempt.get("content_hash") == content_hash
    if not (same_version or same_content) or attempt.get("pipeline_version") != document["pipeline_version"]:
        return None
    if attempt["status"] in ("error", "unavailable", "deferred"):
        return None
    if previous["stale"]:
        # Keep old readable text on disk, but never present it as the current file.
        return {**attempt, **document, "text": "", "pages": []}
    return {**{key: value for key, value in previous.items() if key not in ("latest_attempt", "stale")}, **document}


def ingest_course(course_id, *, store=None, max_files=None, force=False, on_progress=None):
    """Discover one course, extract supported text, and persist reusable results.

    Returns concise per-document outcomes and counters. Read saved text through
    IngestionStore.load_documents(readable_only=True). No binary files are saved.
    This runs explicitly, separately from the dashboard's existing requests.
    """
    from backend.app import document_service
    from backend.app.ingestion_store import IngestionStore

    if not isinstance(course_id, int) or isinstance(course_id, bool) or course_id <= 0:
        raise ValueError("course_id must be a positive integer.")
    if max_files is not None and (not isinstance(max_files, int) or isinstance(max_files, bool) or max_files < 0):
        raise ValueError("max_files must be a non-negative integer or None.")
    store = store or IngestionStore(canvas_service.get_cache_namespace())
    started = time.monotonic()
    snapshot = sync_course(course_id, include_page_bodies=True)
    snapshot["ingestion_status"] = "running"
    store.save_course(snapshot)
    statistics = {key: 0 for key in ("processed", "reused", "downloads", "downloaded_bytes", "readable", "partial", "no_text", "unsupported", "encrypted", "too_large", "unavailable", "error", "deferred")}
    outcomes = []

    def finish(document, action):
        store.save_document(course_id, document, snapshot["synced_at"])
        if action in ("reused", "extracted"):
            statistics["reused" if action == "reused" else "processed"] += 1
        status = document["status"]
        if status in ("ok", "partial") and document.get("text"):
            statistics["readable"] += 1
        if status in statistics:
            statistics[status] += 1
        outcomes.append({
            **{key: document.get(key) for key in ("document_key", "canvas_id", "source_type", "title", "status", "format", "page_count", "slide_count", "block_count", "character_count", "http_status", "error_type")},
            "action": action,
        })

    for record in course_records(snapshot):
        if record["source_type"] not in ("syllabus", "assignment", "page", "quiz", "announcement", "discussion"):
            continue
        html = "\n\n".join(record_html(record))
        document = {
            **{key: record.get(key) for key in ("canvas_id", "source_type", "title", "url", "updated_at", "created_at", "module_id", "module_name")},
            "course_id": course_id,
            "document_key": f"{record['source_type']}:{record['canvas_id']}",
            "version": hashlib.sha256(html.encode()).hexdigest(),
            "pipeline_version": f"{document_service.PIPELINE_VERSION}:html-v2",
        }
        if record["canvas_id"] is None:
            # A slug is a cache key, never an invented Canvas ID.
            document["document_key"] = f"{record['source_type']}:url:{record.get('url')}"
        previous = store.load_document(course_id, document["document_key"])
        cached = _cached_text(previous, document, force)
        if not any(key in record["metadata"] for key in HTML_FIELDS):
            finish({**document, "status": "unavailable", "error_type": "MissingBody", "text": "", "pages": []}, "unavailable")
        elif cached is not None:
            finish(cached, "reused")
        else:
            finish({**document, **document_service.extract_html(html)}, "extracted")

    inventory = snapshot["file_inventory"]
    if on_progress:
        on_progress(f"Discovered {len(inventory)} unique Canvas file references.")
    for index, entry in enumerate(inventory):
        file_id = entry["file_id"]
        document = {
            "document_key": f"file:{file_id}", "canvas_id": file_id,
            "course_id": course_id, "source_type": "file",
            "title": entry["title"] or next((ref["title"] for ref in entry["references"] if ref["title"]), None),
            "url": f"{canvas_service.CANVAS_URL}/courses/{course_id}/files/{file_id}",
            "references": entry["references"], "version": None,
            "pipeline_version": document_service.PIPELINE_VERSION,
        }
        action = "extracted"
        if max_files is not None and index >= max_files:
            finish({**document, "status": "deferred", "text": "", "pages": []}, "deferred")
            continue
        try:
            metadata = entry["metadata"]
            if not metadata or not metadata.get("url"):
                metadata = canvas_service.get_file_info(course_id, file_id)
            if str(metadata.get("id")) != str(file_id):
                raise ValueError("Canvas returned a different file ID.")
            document.update(
                title=metadata.get("display_name") or metadata.get("filename") or document["title"],
                updated_at=metadata.get("updated_at"), created_at=metadata.get("created_at"),
                version=_file_version(metadata),
                pipeline_version=document_service.file_pipeline_version(metadata),
                metadata={key: value for key, value in metadata.items() if key not in ("url", "thumbnail_url")},
            )
            previous = store.load_document(course_id, document["document_key"])
            cached = _cached_text(previous, document, force)
            if cached is not None and cached["status"] == "unsupported" and document_service.file_format(metadata) is not None:
                cached = None  # A newly supported format deserves its first extraction.
                previous = None
            if metadata.get("locked_for_user"):
                document.update(status="unavailable", error_type="LockedForUser", text="", pages=[])
                action = "unavailable"
            elif cached is not None:
                document, action = cached, "reused"
            elif document_service.file_format(metadata) is None:
                document.update(status="unsupported", text="", pages=[], character_count=0)
                action = "unsupported"
            else:
                _, content = canvas_service.download_canvas_file(course_id, file_id, metadata=metadata)
                statistics["downloads"] += 1
                statistics["downloaded_bytes"] += len(content)
                content_hash = hashlib.sha256(content).hexdigest()
                # Some Canvas records have no timestamp. Compare bytes after
                # download so unchanged text still avoids repeated parsing.
                cached = _cached_text(previous, document, force, content_hash=content_hash)
                if cached is not None:
                    document, action = {**cached, **document}, "reused"
                else:
                    document.update(document_service.extract_file(metadata, content))
                del content
        except (requests.RequestException, ValueError) as error:
            code = error.response.status_code if isinstance(error, requests.RequestException) and error.response is not None else None
            document.update(status="unavailable" if code in (403, 404) else "error", http_status=code, error_type=type(error).__name__, text="", pages=[])
            action = "failed"
        finish(document, action)
        if on_progress:
            on_progress(f"File {index + 1}/{len(inventory)}: {document['title'] or file_id}: {document['status']} ({action})")

    statistics["elapsed_seconds"] = round(time.monotonic() - started, 2)
    snapshot["ingestion_status"] = "finished"
    snapshot["coverage"] = "partial" if any(value["status"] != "ok" for value in snapshot["source_status"].values()) or any(doc["status"] != "ok" for doc in outcomes) else "ok"
    snapshot["ingestion_statistics"] = statistics
    store.save_course(snapshot)
    return {"snapshot": snapshot, "documents": outcomes, "statistics": statistics, "database": str(store.path)}


def format_ingestion_summary(result):
    stats = result["statistics"]
    lines = [format_sync_summary(result["snapshot"]), f"Coverage: {result['snapshot']['coverage']}"]
    lines.extend((
        f"Unique file references: {len(result['snapshot']['file_inventory'])}",
        f"Readable documents: {stats['readable']} ({stats['partial']} with incomplete text)",
        f"New/updated documents processed: {stats['processed']}",
        f"Reused unchanged documents: {stats['reused']}",
        f"File downloads: {stats['downloads']} ({stats['downloaded_bytes']} bytes)",
        f"No text: {stats['no_text']}; unsupported: {stats['unsupported']}; encrypted: {stats['encrypted']}; too large: {stats['too_large']}",
        f"Unavailable documents: {stats['unavailable']}; errors: {stats['error']}; deferred: {stats['deferred']}",
        f"Elapsed: {stats['elapsed_seconds']} seconds",
        f"Local database: {result['database']}",
    ))
    return "\n".join(lines)


def ingest_current_courses(*, store=None, on_update=None, force=False):
    """Fetch the dashboard's current course list and ingest every unique course.

    Reports contain counts/status only; document text stays in the store. A
    failed course does not discard successful courses or stop later courses.
    """
    from backend.app.ingestion_store import IngestionStore

    store = store or IngestionStore(canvas_service.get_cache_namespace())
    courses = list({course["id"]: course for course in canvas_service.get_current_courses()}.values())
    results = [{
        "course_id": course["id"], "course_code": course.get("course_code"),
        "name": course.get("name"), "enrollment_term_id": course.get("enrollment_term_id"),
        "status": "pending", "coverage": None, "statistics": {},
    } for course in courses]

    def publish():
        if on_update:
            # Callers may serialize this immediately; never hand out mutable
            # dictionaries that a later course update changes behind their back.
            on_update(json.loads(json.dumps(results)))

    publish()
    for course in results:
        course["status"] = "running"
        publish()
        try:
            report = ingest_course(course["course_id"], store=store, force=force)
            course.update(status="completed", coverage=report["snapshot"]["coverage"], statistics=report["statistics"])
        except Exception as error:
            # Exceptions can contain signed URLs. Store categories, never their
            # raw messages. KeyboardInterrupt deliberately reaches the worker.
            code = error.response.status_code if isinstance(error, requests.RequestException) and error.response is not None else None
            course.update(status="error", coverage="unavailable", error_type=type(error).__name__, http_status=code)
        publish()
    return {
        "courses": results,
        "coverage": "partial" if any(course["coverage"] != "ok" for course in results) else "ok",
        "completed_courses": sum(course["status"] == "completed" for course in results),
        "failed_courses": sum(course["status"] == "error" for course in results),
        "total_courses": len(results),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("course_id", type=int, nargs="?")
    parser.add_argument("--all-current", action="store_true", help="Ingest every current course; requires --ingest.")
    parser.add_argument("--ingest", action="store_true", help="Extract supported documents and save them locally.")
    parser.add_argument("--max-files", type=int, help="Limit file processing for a small first test; requires --ingest.")
    parser.add_argument("--force", action="store_true", help="Reprocess unchanged documents; requires --ingest.")
    args = parser.parse_args()
    if (args.course_id is None) == (not args.all_current):
        parser.error("Provide either a course_id or --all-current.")
    if args.all_current and (not args.ingest or args.max_files is not None):
        parser.error("--all-current requires --ingest and cannot be limited with --max-files.")
    if not args.ingest and (args.max_files is not None or args.force):
        parser.error("--max-files and --force require --ingest")
    try:
        if args.all_current:
            def progress(courses):
                for course in courses:
                    if course["status"] == "running":
                        print(f"Syncing {course['course_code'] or course['course_id']}...", flush=True)
            report = ingest_current_courses(on_update=progress, force=args.force)
            for course in report["courses"]:
                print(f"{course['course_code'] or course['course_id']}: {course['status']}; coverage: {course['coverage']}; readable documents: {course['statistics'].get('readable', 0)}")
            print(f"Courses: {report['completed_courses']}/{report['total_courses']} synced; {report['failed_courses']} failed")
            parser.exit(2 if report["failed_courses"] else 0)
        if args.ingest:
            report = ingest_course(args.course_id, max_files=args.max_files, force=args.force, on_progress=print)
            print(format_ingestion_summary(report))
            parser.exit(2 if report["statistics"]["error"] else 0)
        snapshot = sync_course(args.course_id)
    except requests.RequestException as error:
        code = error.response.status_code if error.response is not None else "connection failure"
        parser.exit(1, f"Course discovery failed: {code}\n")
    except ValueError as error:
        parser.exit(1, f"Course ingestion failed: {error}\n")
    print(format_sync_summary(snapshot))
    if any(status["status"] != "ok" for status in snapshot["source_status"].values()):
        parser.exit(1)
