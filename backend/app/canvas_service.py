import hashlib
import os
import time
from urllib.parse import quote, urlparse

import requests
from dotenv import load_dotenv
from datetime import datetime, timezone

# Load variables from the .env file
# This allows us to keep sensitive information like the Canvas token
# outside of our Python source code.
load_dotenv()

# Read the Canvas token and Canvas website URL from .env
CANVAS_TOKEN = os.getenv("CANVAS_TOKEN")
CANVAS_URL = os.getenv("CANVAS_URL")

# This header is sent with every Canvas API request.
# The token proves to Canvas that we are authorized to access
# the student's Canvas data.
headers = {
    "Authorization": f"Bearer {CANVAS_TOKEN}"
}

REQUEST_TIMEOUT = 60
MAX_REQUEST_ATTEMPTS = 3
MAX_FILE_BYTES = 50 * 1024 * 1024


def get_cache_namespace():
    """Isolate local development caches without storing the access token itself."""
    if not CANVAS_URL or not CANVAS_TOKEN:
        raise ValueError("Set CANVAS_URL and CANVAS_TOKEN in .env before ingesting.")
    return hashlib.sha256(f"{CANVAS_URL.rstrip('/')}\0{CANVAS_TOKEN}".encode()).hexdigest()


class CanvasPartialResponse(requests.RequestException):
    """Retain records collected before a later pagination request failed."""

    def __init__(self, records, error):
        super().__init__("Canvas listing was only partially retrieved", response=error.response)
        self.records = records


def _request_get(url, **kwargs):
    """Bound GET requests and retry temporary failures without retrying denials."""
    kwargs.setdefault("timeout", REQUEST_TIMEOUT)
    for attempt in range(MAX_REQUEST_ATTEMPTS):
        try:
            response = requests.get(url, **kwargs)
        except (requests.Timeout, requests.ConnectionError):
            if attempt == MAX_REQUEST_ATTEMPTS - 1:
                raise
            time.sleep(2 ** attempt)
            continue
        if response.status_code in (429, 500, 502, 503, 504) and attempt < MAX_REQUEST_ATTEMPTS - 1:
            try:
                delay = float(response.headers.get("Retry-After", 2 ** attempt))
            except (TypeError, ValueError):
                delay = 2 ** attempt
            response.close()
            time.sleep(max(0, min(delay, 10)))
            continue
        try:
            response.raise_for_status()
        except requests.RequestException:
            response.close()
            raise
        return response


# ---------------------------------------------------------
# GET COURSES
# ---------------------------------------------------------
def get_courses():
    # Canvas API endpoint for getting the user's courses
    url = f"{CANVAS_URL}/api/v1/courses"
    
    params = {
        "per_page" : 100
    }
    
    return _get_paginated(url, params)


# ---------------------------------------------------------
# GET ASSIGNMENTS FOR ONE COURSE
# ---------------------------------------------------------
def get_assignments(course_id):
    
    # course_id tells Canvas which course we want assignments from.
    #
    # Example:
    # course_id = 278577
    #
    # URL becomes:
    # /api/v1/courses/278577/assignments
    url = f"{CANVAS_URL}/api/v1/courses/{course_id}/assignments"
    
    # Extra options that we send with the API request.
    params = {
        # Ask Canvas to also include the student's
        # submission information for each assignment.
        "include[]": "submission",
        # Canvas normally returns results in pages.
        # Asking for 100 prevents us from only getting
        # the first 10 assignments.
        "per_page": 100
    }
    
    
    return _get_paginated(url, params)


# ---------------------------------------------------------
# CLEAN THE ASSIGNMENT DATA
# ---------------------------------------------------------
def get_clean_assignments(course_id):
    # First get the raw assignment data from Canvas.
    assignments = get_assignments(course_id)
 
    # This list will contain the simpler EasyCanvas version
    # of each assignment.
    clean_assignments = []
    
    # Go through every assignment returned by Canvas.
    for assignment in assignments:
        # Get the student's submission information.
        # If Canvas does not return submission data,
        # use an empty dictionary instead.
        submission = assignment.get("submission") or {}
        
        
        # Canvas gives us many fields that EasyCanvas does not need.
        # We create our own smaller assignment object containing
        # only the useful information.
        clean_assignment = {
            "id": assignment.get("id"),
            "name": assignment.get("name"),
            "due_at": assignment.get("due_at"),
            "points_possible": assignment.get("points_possible"),
            
            # These two values come from the submission object.
            "submitted_at": submission.get("submitted_at"),
            "status": submission.get("workflow_state")
        }
        # Add the cleaned assignment to our list.
        clean_assignments.append(clean_assignment)
    # Sort assignments by due date.
    #
    # lambda tells Python to use "due_at" as the value
    # used for sorting.
    #
    # If an assignment has no due date, "9999" puts it
    # near the end of the list.
    clean_assignments.sort(
      key=lambda assignment: assignment["due_at"] or "9999"
        )
        

    return clean_assignments


# ---------------------------------------------------------
# GET ONLY UPCOMING ASSIGNMENTS
# ---------------------------------------------------------
def get_upcoming_assignments(course_id):
    # Start with our cleaned and sorted assignments.
    assignments = get_clean_assignments(course_id)
    
    # This list will contain only assignments that
    # the student still needs to complete.
    upcoming = []
    
    # Get the current date and time in UTC.
    #
    # Canvas also gives its API dates in UTC,
    # so we can safely compare them.
    now = datetime.now(timezone.utc)
    
    # Check every assignment.
    for assignment in assignments:
        
        # If the assignment is already submitted or graded,
        # skip it and move to the next assignment.
        if assignment["status"] != "unsubmitted":
            continue
        # If the assignment has no due date,
        # do not include it in the upcoming list.
        if assignment["due_at"] is None:
            continue
        
        # Canvas gives the due date as text such as:
        #
        # 2026-10-06T15:00:00Z
        #
        # Convert that text into a Python datetime object
        # so we can compare it with the current time.
        due_date = datetime.fromisoformat(
            assignment["due_at"].replace("Z", "+00:00")
        )
        
        
        # Only keep the assignment if its due date
        # is still in the future.
        if due_date > now:
            upcoming.append(assignment)

    return upcoming


def get_clean_courses():
    return [_clean_course(course) for course in get_courses()]


def _clean_course(course):
    return {
        "id": course.get("id"),
        "name": course.get("name"),
        "course_code": course.get("course_code"),
        "enrollment_term_id": course.get("enrollment_term_id"),
    }


def _get_student_courses():
    """Ask Canvas to apply student enrollment and course/section access dates."""
    return _get_paginated(f"{CANVAS_URL}/api/v1/courses", {
        "per_page": 100, "enrollment_type": "student",
        "enrollment_state": "active", "state[]": "available", "include[]": "term",
    })


def get_course_terms():
    """List the account's accessible student terms without an admin-only API."""
    terms = {}
    for course in _get_student_courses():
        term = course.get("term") or {}
        term_id = course.get("enrollment_term_id")
        if term_id is not None:
            terms.setdefault(term_id, {
                "id": term_id, "name": term.get("name"),
                "start_at": term.get("start_at"), "end_at": term.get("end_at"),
            })
    return list(terms.values())


def _term_is_current(term, now):
    """Return None for unknown dates rather than guessing semester membership."""
    try:
        start = datetime.fromisoformat(term["start_at"].replace("Z", "+00:00"))
        end = datetime.fromisoformat(term["end_at"].replace("Z", "+00:00"))
        if start.tzinfo is None or end.tzinfo is None or end <= start:
            return None
        return start <= now < end
    except (KeyError, AttributeError, TypeError, ValueError):
        return None


def get_current_courses():
    """Select current dated terms, or an explicitly configured account term."""
    requested = (os.getenv("CANVAS_TERM_ID") or "").strip()
    if requested and (not requested.isascii() or not requested.isdecimal() or int(requested) <= 0):
        raise ValueError("CANVAS_TERM_ID must be a positive Canvas enrollment term ID.")
    courses = _get_student_courses()
    if requested:
        selected = [course for course in courses if str(course.get("enrollment_term_id")) == str(int(requested))]
        if courses and not selected:
            raise ValueError("CANVAS_TERM_ID does not match your active student courses. List your terms with get_course_terms().")
    else:
        now = datetime.now(timezone.utc)
        current = [_term_is_current(course.get("term") or {}, now) for course in courses]
        selected = [course for course, matches in zip(courses, current) if matches]
        if courses and not selected and None in current:
            raise ValueError("Canvas term dates are missing or invalid. List your terms with get_course_terms() and set CANVAS_TERM_ID in .env.")
    return [_clean_course(course) for course in selected]


# ---------------------------------------------------------
# DISCOVER COURSE MODULES AND ITEMS
# ---------------------------------------------------------
def get_modules(course_id):
    url = f"{CANVAS_URL}/api/v1/courses/{course_id}/modules"
    params = {"per_page": 100}
    return _get_paginated(url, params)


def get_module_items(course_id, module_id):
    url = f"{CANVAS_URL}/api/v1/courses/{course_id}/modules/{module_id}/items"
    params = {"per_page": 100}
    return _get_paginated(url, params)


def get_course_content(course_id):
    modules = get_modules(course_id)
    course_content = []

    for module in modules:
        module_id = module["id"]
        items = get_module_items(course_id, module_id)
        clean_items = []

        for item in items:
            clean_items.append({
                "id": item.get("id"),
                "title": item.get("title"),
                "type": item.get("type"),
                "content_id": item.get("content_id"),
                "url": item.get("url"),
            })

        course_content.append({
            "module_id": module_id,
            "module_name": module.get("name"),
            "items": clean_items,
        })

    return course_content


# Shared pagination for Canvas list endpoints. Keep next-page URLs intact.
def _get_paginated(url, params=None):
    params = {"per_page": 100, **(params or {})}
    records = []
    visited = set()
    origin = (urlparse(url).scheme, urlparse(url).netloc)
    while url:
        try:
            parsed = urlparse(url)
            if (parsed.scheme, parsed.netloc) != origin or url in visited:
                raise requests.exceptions.InvalidJSONError("Invalid Canvas pagination link")
            visited.add(url)
            response = _request_get(url, headers=headers, params=params)
            page = response.json()
            if not isinstance(page, list) or any(not isinstance(record, dict) for record in page):
                raise requests.exceptions.InvalidJSONError("Expected a list of Canvas records")
        except requests.RequestException as error:
            if records:
                raise CanvasPartialResponse(records, error) from error
            raise
        records.extend(page)
        url = response.links.get("next", {}).get("url")
        params = None
    return records


def get_course_details(course_id):
    """Fetch course metadata and syllabus together in one request."""
    url = f"{CANVAS_URL}/api/v1/courses/{course_id}"
    response = _request_get(url, headers=headers, params={"include[]": "syllabus_body"})
    return response.json()


def get_pages(course_id, include_body=False):
    params = {"include[]": "body"} if include_body else None
    return _get_paginated(f"{CANVAS_URL}/api/v1/courses/{course_id}/pages", params)


def get_page(course_id, page_url):
    """Retrieve a referenced page even when the course Pages index is unavailable."""
    url = f"{CANVAS_URL}/api/v1/courses/{course_id}/pages/{quote(str(page_url), safe='')}"
    return _request_get(url, headers=headers).json()


def get_files(course_id):
    # This lists file metadata; it never requests file contents.
    return _get_paginated(f"{CANVAS_URL}/api/v1/courses/{course_id}/files")


def get_file_info(course_id, file_id):
    """Retrieve one file's metadata independently of the course Files listing."""
    url = f"{CANVAS_URL}/api/v1/courses/{course_id}/files/{file_id}"
    response = _request_get(url, headers=headers)
    return response.json()


def download_canvas_file(course_id, file_id, *, metadata=None, max_bytes=MAX_FILE_BYTES):
    """Return (Canvas metadata, file bytes) without writing anything to disk."""
    if not isinstance(max_bytes, int) or isinstance(max_bytes, bool) or max_bytes <= 0:
        raise ValueError("max_bytes must be a positive integer")
    metadata = get_file_info(course_id, file_id) if metadata is None else metadata
    if metadata.get("id") is not None and str(metadata["id"]) != str(file_id):
        raise ValueError("File metadata does not match the requested file")
    if isinstance(metadata.get("size"), int) and metadata["size"] > max_bytes:
        raise ValueError("File exceeds the configured download limit")
    download_url = metadata.get("url")
    if not download_url:
        raise ValueError("Canvas file metadata does not contain a download URL")

    target = urlparse(download_url)
    canvas = urlparse(CANVAS_URL)
    if target.scheme not in ("http", "https") or not target.hostname:
        raise ValueError("Canvas file metadata contains an invalid download URL")

    def origin(parsed):
        return parsed.scheme, parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80)

    # Signed storage URLs work without the Canvas bearer token. Requests also
    # removes Authorization when a Canvas request redirects to another host.
    download_headers = headers if origin(target) == origin(canvas) else {}
    response = _request_get(
        download_url, headers=download_headers, allow_redirects=True, stream=True
    )
    content = bytearray()
    try:
        for chunk in response.iter_content(chunk_size=64 * 1024):
            if len(content) + len(chunk) > max_bytes:
                raise ValueError("File exceeds the configured download limit")
            content.extend(chunk)
    finally:
        response.close()
    return metadata, bytes(content)


def get_quizzes(course_id):
    return _get_paginated(f"{CANVAS_URL}/api/v1/courses/{course_id}/quizzes")


def get_announcements(course_id):
    # Unlike /announcements, this has no default 14-day date window.
    return _get_paginated(
        f"{CANVAS_URL}/api/v1/courses/{course_id}/discussion_topics",
        {"only_announcements": "true"},
    )


def get_discussions(course_id):
    # Omit this flag entirely: Canvas treats the string "false" as present,
    # which can select announcements instead of ordinary discussion topics.
    return _get_paginated(f"{CANVAS_URL}/api/v1/courses/{course_id}/discussion_topics")
