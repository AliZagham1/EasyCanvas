"""Explicit cleanup of expired course materials; never deletes on a timer."""

import hashlib
import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone

from backend.app import canvas_service
from backend.app.embedding_service import default_store, validate_course_id

GRACE_DAYS = 30


class RetentionConflict(ValueError):
    """The preview is stale or a course cannot safely be removed."""


def _dates(term):
    try:
        start = datetime.fromisoformat(term['start_at'].replace('Z', '+00:00'))
        end = datetime.fromisoformat(term['end_at'].replace('Z', '+00:00'))
        return (start, end) if start.tzinfo and end.tzinfo and end > start else None
    except (KeyError, TypeError, AttributeError, ValueError):
        return None


def _preview(store, connection, current, now):
    """Require trustworthy dates and a completed sync before offering deletion."""
    saved, sync = store.retention_snapshot(connection)
    current_ids = {course['id'] for course in current}
    synced_ids = {course['course_id'] for course in (sync or {}).get('courses', [])
                  if course.get('status') == 'completed'}
    current_dates = [_dates(course.get('term')) for course in current]
    blocked = None
    if os.getenv('CANVAS_TERM_ID', '').strip():
        blocked = 'Clear the fixed semester override before cleaning up old course materials.'
    elif not current or any(dates is None or not (dates[0] <= now < dates[1]) for dates in current_dates):
        blocked = 'Current Canvas courses and semester dates could not be confirmed. Nothing can be deleted.'
    elif not sync or sync.get('status') != 'completed' or sync.get('failed_courses', 0) or current_ids != synced_ids:
        blocked = 'Finish a successful sync of all current courses before cleaning up old materials.'
    courses = []
    for item in saved:
        snapshot = item['snapshot']
        course_id = snapshot['course_id']
        meta = snapshot.get('course', {}).get('metadata', {})
        dates = _dates(meta.get('term'))
        eligible_after = dates[1] + timedelta(days=GRACE_DAYS) if dates else None
        if snapshot.get('materials_removed'):
            reason = 'Course materials already removed.'
        elif course_id in current_ids:
            reason = 'Current course.'
        elif item['kept']:
            reason = 'You chose to keep this course.'
        elif not dates:
            reason = 'Semester dates are missing or invalid; materials are protected.'
        elif now < eligible_after:
            reason = 'The semester has not ended or the 30-day grace period is still active.'
        else:
            reason = blocked
        courses.append({'course_id': course_id, 'title': meta.get('course_code') or meta.get('name') or str(course_id),
                        'name': meta.get('name'), 'term_name': meta['term'].get('name') if isinstance(meta.get('term'), dict) else None,
                        'term_end': dates[1].isoformat() if dates else None,
                        'eligible_after': eligible_after.isoformat() if eligible_after else None,
                        'kept': item['kept'], 'eligible': reason is None, 'reason': reason,
                        'materials_removed': bool(snapshot.get('materials_removed')),
                        'counts': item['counts'], 'estimated_bytes': item['estimated_bytes'],
                        '_revision': item['revision']})
    courses.sort(key=lambda course: course['course_id'])
    identity = json.dumps({'namespace': store.namespace, 'courses': courses, 'current': sorted(current_ids),
                           'sync': sync}, sort_keys=True)
    fingerprint = hashlib.sha256(identity.encode()).hexdigest()
    for course in courses:
        course.pop('_revision')
    return {'preview_id': fingerprint, 'grace_days': GRACE_DAYS, 'blocked_reason': blocked,
            'courses': courses, 'eligible_bytes': sum(c['estimated_bytes'] for c in courses if c['eligible']),
            'chats_preserved': True}


def preview_cleanup(*, store=None, now=None):
    """Fetch current Canvas identities, then return a preview without deleting data."""
    store = store or default_store()
    current = canvas_service.get_current_courses()
    with store._connection() as connection:
        connection.execute('BEGIN')
        return _preview(store, connection, current, now or datetime.now(timezone.utc))


def delete_materials(course_ids, preview_id, *, store=None, now=None):
    """Recheck the preview and eligibility atomically before removing material rows."""
    if not course_ids or len(course_ids) > 100 or len(set(course_ids)) != len(course_ids):
        raise ValueError('Select between 1 and 100 distinct courses.')
    for course_id in course_ids:
        validate_course_id(course_id)
    store = store or default_store()
    current = canvas_service.get_current_courses()  # Failure aborts cleanup.
    with store._connection() as connection:
        connection.execute('BEGIN IMMEDIATE')
        preview = _preview(store, connection, current, now or datetime.now(timezone.utc))
        if preview['preview_id'] != preview_id:
            raise RetentionConflict('Storage or sync state changed. Refresh the preview and try again.')
        eligible = {course['course_id'] for course in preview['courses'] if course['eligible']}
        if not set(course_ids) <= eligible:
            raise RetentionConflict('Only expired, unprotected course materials can be deleted.')
        store.remove_course_materials(connection, course_ids)
    compacted = True
    try:
        store.compact()
    except sqlite3.OperationalError:
        compacted = False  # Deletion succeeded; a busy database can reuse freed pages.
    return {'deleted_course_ids': course_ids, 'chats_preserved': True, 'compacted': compacted}
