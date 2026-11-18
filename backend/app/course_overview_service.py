"""Course pages: what we already know about each course, assembled from saved data only.

No Canvas request, no AI, no waiting for a sync. Assignment status comes from the Priorities endpoint and class times
from the schedule; this module adds the saved announcements, modules/materials, counts and Canvas links.
"""

import html
import re

from backend.app import canvas_service, schedule_service

MAX_EXCERPT = 240
MAX_ANNOUNCEMENTS = 15
_BLOCKS = re.compile(r"<(script|style)\b.*?</\1\s*>", re.I | re.S)
_TAGS = re.compile(r"<[^>]*>")


def plain_text(value, limit=MAX_EXCERPT):
    """Announcement HTML as safe plain text: no tags, no scripts, entities decoded, whitespace collapsed, shortened."""
    if not isinstance(value, str):
        return ""
    text = html.unescape(_TAGS.sub(" ", _BLOCKS.sub(" ", value)))
    text = re.sub(r"\s+", " ", text).strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def canvas_course_url(course_id):
    base = (canvas_service.CANVAS_URL or "").rstrip("/")
    return f"{base}/courses/{course_id}" if base else None


def _snapshot(store, course_id):
    return store.load_course(course_id) or {}


def _count(snapshot, key):
    value = snapshot.get(key)
    return len(value) if isinstance(value, list) else 0


def _info(store, course_id):
    return next((info for info in schedule_service._course_infos(store) if info["course_id"] == course_id), None)


def _posted(record):
    meta = record.get("metadata") or {}
    return meta.get("posted_at") or record.get("created_at") or record.get("updated_at")


def _announcements(snapshot):
    items = [{"title": record.get("title") or "Announcement", "url": record.get("url"), "posted_at": _posted(record),
              "excerpt": plain_text((record.get("metadata") or {}).get("message"))}
             for record in snapshot.get("announcements") or []]
    items.sort(key=lambda item: item["posted_at"] or "", reverse=True)
    return items


def _modules(snapshot):
    """Modules in Canvas order with their items (headers and unpublished items left out)."""
    items_by_module = {}
    for record in snapshot.get("module_items") or []:
        meta = record.get("metadata") or {}
        kind = meta.get("type") or record.get("item_type") or "Item"
        if kind == "SubHeader" or meta.get("published") is False:
            continue
        items_by_module.setdefault(record.get("module_id"), []).append(
            {"title": record.get("title") or "Untitled", "type": kind, "url": record.get("url") or meta.get("html_url"),
             "position": meta.get("position") or 0})
    modules = []
    for index, record in enumerate(snapshot.get("modules") or []):
        meta = record.get("metadata") or {}
        if meta.get("published") is False:
            continue
        # A saved module record identifies itself by canvas_id (its module_id field is empty); items point at that id.
        module_id = record.get("canvas_id") or meta.get("id") or record.get("module_id") or f"position-{index}"
        items = sorted(items_by_module.get(module_id, []), key=lambda item: item["position"])
        modules.append({"module_id": module_id, "name": record.get("title") or meta.get("name") or "Module",
                        "position": meta.get("position") or 0,
                        "items": [{key: value for key, value in item.items() if key != "position"} for item in items]})
    modules.sort(key=lambda module: module["position"])
    return [{key: value for key, value in module.items() if key != "position"} for module in modules]


def _meetings(store, course_id):
    return [meeting for meeting in store.list_class_meetings("confirmed") if meeting["course_id"] == course_id]


def _counts(snapshot):
    files = max(_count(snapshot, "files"), _count(snapshot, "file_inventory"))
    return {"assignments": _count(snapshot, "assignments"), "modules": _count(snapshot, "modules"), "files": files,
            "pages": _count(snapshot, "pages"), "quizzes": _count(snapshot, "quizzes"),
            "announcements": _count(snapshot, "announcements")}


def build_catalog(store):
    """One summary per saved current course, for the Courses page."""
    courses = []
    for info in schedule_service._course_infos(store):
        snapshot = _snapshot(store, info["course_id"])
        announcements = _announcements(snapshot)
        courses.append({**info, "canvas_url": canvas_course_url(info["course_id"]), "synced_at": snapshot.get("synced_at"),
                        "counts": _counts(snapshot), "meetings": _meetings(store, info["course_id"]),
                        "latest_announcement": ({"title": announcements[0]["title"], "posted_at": announcements[0]["posted_at"]}
                                                if announcements else None)})
    return {"courses": courses}


def build_details(store, course_id):
    """Everything saved about one course, for its page. Raises LookupError for an unknown course."""
    info = _info(store, course_id)
    if info is None:
        raise LookupError("Course not found. Sync Canvas first.")
    snapshot = _snapshot(store, course_id)
    url = canvas_course_url(course_id)
    return {**info, "canvas_url": url, "syllabus_url": f"{url}/assignments/syllabus" if url else None,
            "synced_at": snapshot.get("synced_at"), "counts": _counts(snapshot), "meetings": _meetings(store, course_id),
            "announcements": _announcements(snapshot)[:MAX_ANNOUNCEMENTS], "modules": _modules(snapshot)}
