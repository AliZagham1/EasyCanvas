"""Local development storage; no Canvas requests or document parsing live here."""

import json
import hashlib
import math
import sqlite3
import struct
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_DATABASE = Path(__file__).resolve().parents[1] / "data" / "easycanvas.sqlite3"
TEXT_COLLECTIONS = ("pages", "slides", "rendered_pages", "blocks")
MATERIAL_TABLES = {
    'search_index_jobs': 'length(state_json)',
    'documents': 'length(CAST(document_json AS BLOB)) + length(CAST(attempt_json AS BLOB))',
    'document_chunks': 'length(CAST(text AS BLOB)) + length(CAST(locations_json AS BLOB))',
    'document_chunk_state': 'length(input_hash) + length(chunker_version)',
    'chunk_embeddings': 'length(vector_blob) + length(config_json)',
    'visual_extractions': 'length(CAST(result_json AS BLOB))',
    'document_served_versions': 'length(CAST(document_json AS BLOB)) + length(CAST(chunks_json AS BLOB))',
}
READ_FIELDS = ("latest_attempt", "stale", "content_status", "refresh_status",
               "content_usable", "cache_status", "last_refresh_error", "newer_version_unavailable",
               "served_version")
# Reasons that must hide saved text until a later successful processing clears
# them. Older databases may also contain the retired soft reasons
# 'processing_unavailable'/'changed_content'; those no longer block content.
HARD_BLOCK_REASONS = {'access_revoked', 'removed', 'access_unavailable', 'superseded', 'invalid_content'}
PENDING_REFRESH_STATES = ('running', 'failed', 'interrupted')


def _temporary_refresh_failure(attempt):
    """Recognize network/retryable HTTP failures, never parser or access errors."""
    if attempt.get('status') != 'error':
        return False
    code = attempt.get('http_status')
    if code is not None:
        return code in (408, 429, 500, 502, 503, 504)
    return attempt.get('error_type') in (
        'ConnectionError', 'Timeout', 'ConnectTimeout', 'ReadTimeout',
        'ProxyError', 'ChunkedEncodingError',
    )


def _preservable_failure(attempt):
    """A failed refresh that does not show the saved text is inaccessible or replaced.

    Network, provider, parser and unexpected processing errors keep the last good
    extraction usable (clearly marked stale). Canvas access/removal answers do not.
    """
    if attempt.get('status') == 'deferred':
        return True
    return attempt.get('status') == 'error' and attempt.get('http_status') not in (401, 403, 404, 410)


def _refresh_block_reason(document, attempt):
    """Hide saved text only for confirmed access loss or a successful replacement."""
    code = attempt.get('http_status')
    if code in (401, 403) or attempt.get('error_type') == 'LockedForUser':
        return 'access_revoked'
    if code in (404, 410):
        return 'removed'
    if attempt.get('status') in ('ok', 'partial') or _preservable_failure(attempt):
        return None
    if attempt.get('status') == 'unavailable':
        return 'access_unavailable'
    # no_text/unsupported/encrypted: the newest content was processed and has no
    # readable text, so older text must not stand in for it.
    return 'superseded'


def _hard_block(value):
    return value if value in HARD_BLOCK_REASONS else None


def _parse_time(value):
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else None


def pending_refresh(snapshot):
    """Return the uncommitted refresh marker for a published snapshot, if any."""
    marker = (snapshot or {}).get('refresh') or {}
    return marker if marker.get('status') in PENDING_REFRESH_STATES else None


def material_refresh(snapshot):
    """Describe an in-progress/unfinished course sync for status and answer notices.

    `previous_material` is False only for a first sync, whose documents appear as
    they are processed because no earlier published generation exists.
    """
    marker = pending_refresh(snapshot)
    if marker:
        return {'state': marker['status'], 'previous_material': True,
                'started_at': marker.get('started_at'), 'published_at': (snapshot or {}).get('synced_at')}
    status = (snapshot or {}).get('ingestion_status')
    if status in PENDING_REFRESH_STATES:
        return {'state': status, 'previous_material': False, 'started_at': (snapshot or {}).get('synced_at'),
                'published_at': None}
    return None


def visible_in_snapshot(last_seen_at, snapshot):
    """Keep the last published generation visible until a new sync commits.

    Without a pending refresh only the published `synced_at` is current. While a
    refresh is running, failed or interrupted, documents restamped by that
    uncommitted work are also current: each key has one row holding its latest
    state, so nothing is duplicated and confirmed access loss still applies.
    Documents last seen before the published sync remain excluded.
    """
    published = snapshot.get('synced_at')
    if last_seen_at == published:
        return True
    marker = pending_refresh(snapshot)
    if not marker:
        return False
    if last_seen_at == marker.get('synced_at'):
        return True
    seen, base = _parse_time(last_seen_at), _parse_time(published)
    return bool(seen and base and seen > base)


def validate_vector(vector, dimensions):
    """Reject malformed, non-finite, or zero vectors before storage/comparison."""
    if not isinstance(dimensions, int) or isinstance(dimensions, bool) or dimensions <= 0:
        raise ValueError("Embedding dimensions must be a positive integer.")
    if not isinstance(vector, (list, tuple)) or len(vector) != dimensions:
        raise ValueError("Embedding dimensions do not match configuration.")
    if any(isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) for value in vector):
        raise ValueError("Embedding contains invalid numerical values.")
    norm = math.hypot(*vector)
    if not math.isfinite(norm) or not norm:
        raise ValueError("Embedding must have a finite nonzero norm.")


def encode_vector(vector, dimensions):
    """Store explicit little-endian float32 values, independent of computer OS."""
    validate_vector(vector, dimensions)
    try:
        data = struct.pack(f"<{dimensions}f", *vector)
    except (OverflowError, struct.error):
        raise ValueError("Embedding cannot be stored as float32.") from None
    decode_vector(data, dimensions)
    return data


def decode_vector(data, dimensions):
    if len(data) != dimensions * 4:
        raise ValueError("Stored embedding has invalid byte length.")
    vector = struct.unpack(f"<{dimensions}f", data)
    validate_vector(vector, dimensions)
    return vector


class IngestionStore:
    """Keep course snapshots and extracted text across Python restarts.

    The caller supplies an account namespace so different development tokens do
    not reuse each other's private course data. This is not production login.
    """

    def __init__(self, namespace, path=DEFAULT_DATABASE):
        self.namespace = namespace
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with self._connection() as connection:
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS courses (
                    namespace TEXT NOT NULL,
                    course_id INTEGER NOT NULL,
                    snapshot_json TEXT NOT NULL,
                    PRIMARY KEY (namespace, course_id)
                );
                CREATE TABLE IF NOT EXISTS documents (
                    namespace TEXT NOT NULL,
                    course_id INTEGER NOT NULL,
                    document_key TEXT NOT NULL,
                    document_json TEXT NOT NULL,
                    attempt_json TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    PRIMARY KEY (namespace, course_id, document_key)
                );
                CREATE TABLE IF NOT EXISTS course_sync (
                    namespace TEXT PRIMARY KEY,
                    state_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS course_retention (
                    namespace TEXT NOT NULL, course_id INTEGER NOT NULL,
                    keep_materials INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (namespace, course_id)
                );
                CREATE TABLE IF NOT EXISTS document_chunk_state (
                    namespace TEXT NOT NULL,
                    course_id INTEGER NOT NULL,
                    document_key TEXT NOT NULL,
                    input_hash TEXT NOT NULL,
                    chunker_version TEXT NOT NULL,
                    chunk_count INTEGER NOT NULL,
                    processed_at TEXT NOT NULL,
                    PRIMARY KEY (namespace, course_id, document_key)
                );
                CREATE TABLE IF NOT EXISTS document_chunks (
                    namespace TEXT NOT NULL,
                    course_id INTEGER NOT NULL,
                    document_key TEXT NOT NULL,
                    chunk_index INTEGER NOT NULL,
                    input_hash TEXT NOT NULL,
                    chunker_version TEXT NOT NULL,
                    text TEXT NOT NULL,
                    locations_json TEXT NOT NULL,
                    PRIMARY KEY (namespace, course_id, document_key, chunk_index)
                );
                CREATE TABLE IF NOT EXISTS chunk_embeddings (
                    namespace TEXT NOT NULL,
                    course_id INTEGER NOT NULL,
                    text_hash TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    model TEXT NOT NULL,
                    config_hash TEXT NOT NULL,
                    config_json TEXT NOT NULL,
                    dimensions INTEGER NOT NULL,
                    vector_blob BLOB NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (namespace, course_id, text_hash, provider, model, config_hash)
                );
                CREATE TABLE IF NOT EXISTS visual_extractions (
                    namespace TEXT NOT NULL,
                    course_id INTEGER NOT NULL,
                    content_hash TEXT NOT NULL,
                    unit_kind TEXT NOT NULL,
                    unit_number INTEGER NOT NULL,
                    config_hash TEXT NOT NULL,
                    result_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (namespace, course_id, content_hash, unit_kind, unit_number, config_hash)
                );
                CREATE TABLE IF NOT EXISTS app_settings (
                    namespace TEXT PRIMARY KEY, settings_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS search_index_jobs (
                    namespace TEXT NOT NULL, course_id INTEGER NOT NULL,
                    state_json TEXT NOT NULL,
                    PRIMARY KEY (namespace, course_id)
                );
                CREATE TABLE IF NOT EXISTS document_served_versions (
                    namespace TEXT NOT NULL, course_id INTEGER NOT NULL,
                    document_key TEXT NOT NULL, document_json TEXT NOT NULL,
                    attempt_json TEXT NOT NULL, chunks_json TEXT NOT NULL,
                    input_hash TEXT NOT NULL, chunker_version TEXT NOT NULL,
                    saved_at TEXT NOT NULL,
                    PRIMARY KEY (namespace, course_id, document_key)
                );
                CREATE TABLE IF NOT EXISTS api_usage (
                    namespace TEXT NOT NULL, operation_id TEXT NOT NULL,
                    recorded_at TEXT NOT NULL, operation TEXT NOT NULL,
                    feature TEXT NOT NULL, model TEXT NOT NULL,
                    input_tokens INTEGER, output_tokens INTEGER,
                    cached_input_tokens INTEGER, estimated_cost_usd REAL,
                    pricing_version TEXT NOT NULL,
                    PRIMARY KEY (namespace, operation_id)
                );
            """)
        self.path.chmod(0o600)

    @contextmanager
    def _connection(self):
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def save_course(self, snapshot):
        """Save discovery and source coverage, including partial-source failures."""
        with self._connection() as connection:
            connection.execute(
                "INSERT OR REPLACE INTO courses VALUES (?, ?, ?)",
                (self.namespace, snapshot["course_id"], json.dumps(snapshot)),
            )

    def load_course(self, course_id):
        with self._connection() as connection:
            row = connection.execute(
                "SELECT snapshot_json FROM courses WHERE namespace = ? AND course_id = ?",
                (self.namespace, course_id),
            ).fetchone()
        return json.loads(row["snapshot_json"]) if row else None

    def recover_interrupted_refreshes(self):
        """Mark refreshes left 'running' by a stopped/killed worker as interrupted.

        Only the single worker (holding the sync lock) calls this before it starts
        work. Saved documents are untouched: published and partially refreshed
        material stays visible until the next sync commits.
        """
        recovered = []
        now = datetime.now(timezone.utc).isoformat()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            for row in connection.execute("SELECT course_id, snapshot_json FROM courses WHERE namespace=?",
                                          (self.namespace,)).fetchall():
                snapshot = json.loads(row["snapshot_json"])
                marker = snapshot.get('refresh') or {}
                if marker.get('status') == 'running':
                    snapshot['refresh'] = {**marker, 'status': 'interrupted', 'finished_at': now}
                elif snapshot.get('ingestion_status') == 'running':
                    snapshot['ingestion_status'] = 'interrupted'
                else:
                    continue
                connection.execute("UPDATE courses SET snapshot_json=? WHERE namespace=? AND course_id=?",
                                   (json.dumps(snapshot), self.namespace, row["course_id"]))
                recovered.append(row["course_id"])
        return recovered

    def retention_snapshot(self, connection):
        """Read account-scoped material sizes and retention choices in one transaction."""
        rows = connection.execute('SELECT course_id, snapshot_json FROM courses WHERE namespace=?',
                                  (self.namespace,)).fetchall()
        kept = {r['course_id'] for r in connection.execute(
            'SELECT course_id FROM course_retention WHERE namespace=? AND keep_materials=1', (self.namespace,))}
        courses = []
        for row in rows:
            counts, size = {}, len(row['snapshot_json'].encode('utf-8'))
            for table, expression in MATERIAL_TABLES.items():
                count, payload = connection.execute(
                    f'SELECT count(*), coalesce(sum({expression}), 0) FROM {table} WHERE namespace=? AND course_id=?',
                    (self.namespace, row['course_id'])).fetchone()
                counts[table] = count
                size += payload
            courses.append({'snapshot': json.loads(row['snapshot_json']), 'kept': row['course_id'] in kept,
                            'counts': counts, 'estimated_bytes': size,
                            'revision': hashlib.sha256(row['snapshot_json'].encode()).hexdigest()})
        state = connection.execute('SELECT state_json FROM course_sync WHERE namespace=?', (self.namespace,)).fetchone()
        return courses, json.loads(state['state_json']) if state else None

    def set_course_kept(self, course_id, kept):
        """Persist the user's choice without changing ingestion or current-course selection."""
        with self._connection() as connection:
            if not connection.execute('SELECT 1 FROM courses WHERE namespace=? AND course_id=?',
                                      (self.namespace, course_id)).fetchone():
                raise ValueError('Saved course not found.')
            connection.execute('INSERT OR REPLACE INTO course_retention VALUES (?, ?, ?)',
                               (self.namespace, course_id, int(kept)))

    def remove_course_materials(self, connection, course_ids):
        """Delete selected material rows; retain minimal course identity and all chats."""
        for course_id in course_ids:
            row = connection.execute('SELECT snapshot_json FROM courses WHERE namespace=? AND course_id=?',
                                     (self.namespace, course_id)).fetchone()
            snapshot = json.loads(row['snapshot_json'])
            metadata = snapshot.get('course', {}).get('metadata', {})
            retained = {'course_id': course_id, 'synced_at': snapshot.get('synced_at'),
                        'materials_removed': True, 'course': {'metadata': {
                            key: metadata.get(key) for key in ('id', 'name', 'course_code', 'enrollment_term_id', 'term')}}}
            for table in MATERIAL_TABLES:
                connection.execute(f'DELETE FROM {table} WHERE namespace=? AND course_id=?', (self.namespace, course_id))
            connection.execute('UPDATE courses SET snapshot_json=? WHERE namespace=? AND course_id=?',
                               (json.dumps(retained), self.namespace, course_id))

    def compact(self):
        """Return unused SQLite pages to disk after an explicitly requested cleanup."""
        connection = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        try:
            connection.execute('VACUUM')
        finally:
            connection.close()

    def load_sync_state(self):
        """Read the current account's small, persistent background-sync report."""
        with self._connection() as connection:
            row = connection.execute("SELECT state_json FROM course_sync WHERE namespace = ?", (self.namespace,)).fetchone()
        return json.loads(row["state_json"]) if row else None

    def save_sync_state(self, state):
        with self._connection() as connection:
            connection.execute("INSERT OR REPLACE INTO course_sync VALUES (?, ?)", (self.namespace, json.dumps(state)))

    def queue_sync(self, reason="manual", *, reset=False):
        """Coalesce duplicate clicks/requests into the already queued or running job."""
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT state_json FROM course_sync WHERE namespace = ?", (self.namespace,)).fetchone()
            previous = json.loads(row["state_json"]) if row else None
            if previous and previous["status"] in ("queued", "running") and not reset:
                return previous
            state = {
                "run_id": uuid.uuid4().hex, "status": "queued", "reason": reason,
                "queued_at": datetime.now(timezone.utc).isoformat(),
                "started_at": None, "finished_at": None, "next_sync_at": None,
                "total_courses": None, "completed_courses": 0, "failed_courses": 0,
                "current_course_id": None, "courses": [], "coverage": None,
                # Keep the last known course list while startup discovery is pending.
                "previous_course_ids": [course['course_id'] for course in (previous or {}).get('courses', [])]
                    or (previous or {}).get('previous_course_ids', []),
            }
            connection.execute("INSERT OR REPLACE INTO course_sync VALUES (?, ?)", (self.namespace, json.dumps(state)))
        return state

    def save_document(self, course_id, document, seen_at):
        """Save one attempt without erasing previously readable text on failure."""
        from backend.app.document_quality_service import assess_document_quality
        document = {key: value for key, value in document.items() if key not in READ_FIELDS}
        document = assess_document_quality(document)
        attempt = {key: value for key, value in document.items() if key not in ("text", *TEXT_COLLECTIONS)}
        attempt["attempted_at"] = datetime.now(timezone.utc).isoformat()
        payload = dict(document)
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM documents WHERE namespace = ? AND course_id = ? AND document_key = ?",
                (self.namespace, course_id, document["document_key"]),
            ).fetchone()
        previous = self._decode(row) if row else None
        if document["status"] not in ("ok", "partial") and previous and previous.get("text"):
            payload = {key: value for key, value in previous.items() if key not in READ_FIELDS}
            # A later timeout must not resurrect content already known revoked,
            # removed, superseded, or invalid. Only a successful processing clears it.
            payload['cache_blocked'] = _hard_block(previous.get('cache_blocked')) or _refresh_block_reason(payload, attempt)
        elif document['status'] in ('ok', 'partial'):
            payload.pop('cache_blocked', None)
            payload['last_successful_refresh'] = attempt['attempted_at']
        # Store text once in its original units, then join it again on read.
        if any(payload.get(key) for key in TEXT_COLLECTIONS):
            payload.pop("text", None)
        if previous and previous['content_usable']:
            self._retain_served_version(course_id, row, previous, payload, attempt)
        with self._connection() as connection:
            connection.execute(
                "INSERT OR REPLACE INTO documents VALUES (?, ?, ?, ?, ?, ?)",
                (self.namespace, course_id, document["document_key"], json.dumps(payload), json.dumps(attempt), seen_at),
            )

    # ------------------------------------------------------------------
    # Previous searchable versions
    # ------------------------------------------------------------------
    def _embedded_hashes(self, course_id, configuration=None):
        """Text hashes with a vector for the given (default) embedding configuration."""
        from backend.app.embedding_service import EmbeddingConfiguration
        configuration = configuration or EmbeddingConfiguration()
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT text_hash FROM chunk_embeddings WHERE namespace=? AND course_id=? AND provider=? AND model=? AND config_hash=?",
                (self.namespace, course_id, configuration.provider, configuration.model, configuration.fingerprint),
            ).fetchall()
        return {row[0] for row in rows}

    @staticmethod
    def _chunk_set_ready(document, rows, state, embedded):
        """True when this exact extraction is chunked and every eligible chunk is embedded."""
        from backend.app import chunking_service
        if not state or state['input_hash'] != chunking_service.chunk_input_hash(document) \
                or state['chunker_version'] != chunking_service.CHUNKER_VERSION:
            return False
        if len(rows) != state['chunk_count'] or any(row['input_hash'] != state['input_hash'] for row in rows):
            return False
        for row in rows:
            chunk = {'text': row['text'], 'locations': json.loads(row['locations_json'])}
            metadata = chunking_service.chunk_metadata(document, chunk)
            if metadata['embedding_eligible'] and metadata['text_hash'] not in embedded:
                return False
        return True

    def _document_chunk_rows(self, course_id, document_key):
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM document_chunks WHERE namespace=? AND course_id=? AND document_key=? ORDER BY chunk_index",
                (self.namespace, course_id, document_key)).fetchall()
            state = connection.execute(
                "SELECT * FROM document_chunk_state WHERE namespace=? AND course_id=? AND document_key=?",
                (self.namespace, course_id, document_key)).fetchone()
        return [dict(row) for row in rows], dict(state) if state else None

    def _retain_served_version(self, course_id, row, previous, payload, attempt):
        """Keep the replaced version searchable until its replacement is ready.

        Called before a usable document is overwritten. Only a different chunk
        input is a replacement. The outgoing version is stored only if it is fully
        chunked and embedded; otherwise any older ready copy is kept unchanged.
        """
        from backend.app.chunking_service import chunk_input_hash
        incoming = self._decode({'document_json': json.dumps(payload), 'attempt_json': json.dumps(attempt)})
        if not incoming['content_usable'] or chunk_input_hash(incoming) == chunk_input_hash(previous):
            return
        rows, state = self._document_chunk_rows(course_id, previous['document_key'])
        if not self._chunk_set_ready(previous, rows, state, self._embedded_hashes(course_id)):
            return
        with self._connection() as connection:
            connection.execute(
                "INSERT OR REPLACE INTO document_served_versions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (self.namespace, course_id, previous['document_key'], row['document_json'], row['attempt_json'],
                 json.dumps(rows), state['input_hash'], state['chunker_version'], datetime.now(timezone.utc).isoformat()))

    def _apply_served_versions(self, course_id, documents, configuration=None):
        """Substitute a retained ready version while a replacement is not yet searchable.

        Never substitutes for a hard-blocked current document (revoked, removed,
        unavailable, superseded or invalid), so inaccessible material stays hidden.
        """
        stored = {row['document_key']: row for row in self._served_version_rows(course_id)}
        if not stored:
            return documents
        from backend.app import chunking_service
        embedded = self._embedded_hashes(course_id, configuration)
        result = []
        for document in documents:
            retained = stored.get(document['document_key'])
            if retained is None or document.get('cache_blocked') or retained['chunker_version'] != chunking_service.CHUNKER_VERSION:
                result.append(document)
                continue
            rows, state = self._document_chunk_rows(course_id, document['document_key'])
            if document['content_usable'] and self._chunk_set_ready(document, rows, state, embedded):
                result.append(document)
                continue
            previous = self._decode(retained)
            previous_rows = [{**chunk, 'course_id': course_id} for chunk in json.loads(retained['chunks_json'])]
            previous_state = {'input_hash': retained['input_hash'], 'chunker_version': retained['chunker_version'],
                              'chunk_count': len(previous_rows)}
            if not previous['content_usable'] or not self._chunk_set_ready(previous, previous_rows, previous_state, embedded):
                result.append(document)
                continue
            # Current identity/provenance/module links, previous searchable text.
            previous.update({key: document.get(key) for key in ('title', 'url', 'references', 'module_id', 'module_name', 'source_role')
                             if key in document})
            previous['served_version'] = 'previous'
            previous['_served_chunk_rows'] = previous_rows
            result.append(previous)
        return result

    def _served_version_rows(self, course_id):
        """Read retained versions; a database not yet migrated simply has none.

        Read-only stores (for example the live evaluation) skip constructor
        migrations, so the table may legitimately be missing there.
        """
        try:
            with self._connection() as connection:
                return connection.execute(
                    "SELECT * FROM document_served_versions WHERE namespace=? AND course_id=?",
                    (self.namespace, course_id)).fetchall()
        except sqlite3.OperationalError as error:
            if "no such table" in str(error):
                return []
            raise

    def prune_served_versions(self, course_id, configuration=None):
        """Delete retained versions whose replacement is ready, blocked or gone."""
        keys = [row['document_key'] for row in self._served_version_rows(course_id)]
        if not keys:
            return 0
        current = {doc['document_key']: doc for doc in self._documents(course_id)}
        embedded = self._embedded_hashes(course_id, configuration)
        stale = []
        for key in keys:
            document = current.get(key)
            if document is None or document.get('cache_blocked'):
                stale.append(key)
                continue
            rows, state = self._document_chunk_rows(course_id, key)
            if document['content_usable'] and self._chunk_set_ready(document, rows, state, embedded):
                stale.append(key)
        with self._connection() as connection:
            connection.executemany("DELETE FROM document_served_versions WHERE namespace=? AND course_id=? AND document_key=?",
                                   [(self.namespace, course_id, key) for key in stale])
        return len(stale)

    def reassess_document_quality(self, course_id):
        """Correct current saved quality metadata without altering source attempts.

        Compare-and-swap avoids overwriting a concurrent refresh. Unchanged,
        failed, removed and stale documents are left alone; no source is fetched.
        """
        from backend.app.document_quality_service import assess_document_quality
        snapshot = self.load_course(course_id)
        if not snapshot:
            raise ValueError("No saved course snapshot for this account/course.")
        report = {"changed": 0, "promoted": 0, "unchanged": 0, "skipped": 0}
        with self._connection() as connection:
            rows = [row for row in connection.execute(
                "SELECT * FROM documents WHERE namespace = ? AND course_id = ?",
                (self.namespace, course_id),
            ).fetchall() if visible_in_snapshot(row["last_seen_at"], snapshot)]
            for row in rows:
                document = self._decode(row)
                if document["stale"] or document.get("status") not in ("ok", "partial") or document['latest_attempt'].get('status') not in ('ok', 'partial'):
                    report["skipped"] += 1
                    continue
                updated = assess_document_quality(document)
                if document == updated:
                    report["unchanged"] += 1
                    continue
                payload = json.loads(row["document_json"])
                for key in ("pages", "rendered_pages", "slides", "status"):
                    if key in updated:
                        payload[key] = updated[key]
                attempt = json.loads(row["attempt_json"])
                attempt["status"] = updated["status"]
                changed = connection.execute(
                    "UPDATE documents SET document_json = ?, attempt_json = ? "
                    "WHERE namespace = ? AND course_id = ? AND document_key = ? AND document_json = ? AND attempt_json = ? AND last_seen_at = ?",
                    (json.dumps(payload), json.dumps(attempt), self.namespace, course_id,
                     document["document_key"], row["document_json"], row["attempt_json"], row["last_seen_at"]),
                ).rowcount
                report["changed"] += changed
                report["promoted"] += changed * (document["status"] == "partial" and updated["status"] == "ok")
        return report

    @staticmethod
    def _decode(row):
        document = json.loads(row["document_json"])
        if document.get("source_type") == "syllabus" and not document.get("url"):
            from backend.app.content_discovery import source_url
            document["url"] = source_url(document)
        for key in TEXT_COLLECTIONS:
            if document.get(key):
                units = document[key]
                if not isinstance(units, list) or any(not isinstance(unit, dict) or not isinstance(unit.get('text', ''), str) for unit in units):
                    document['text'] = ''
                    document['cache_blocked'] = 'invalid_content'
                else:
                    document["text"] = "\n\n".join(unit["text"] for unit in units if unit.get("text"))
                break
        attempt = json.loads(row["attempt_json"])
        document["latest_attempt"] = attempt
        mismatch = document.get("status") != attempt.get("status") or document.get("version") != attempt.get("version")
        blocked = _hard_block(document.get('cache_blocked')) or _refresh_block_reason(document, attempt)
        valid = (document.get('status') in ('ok', 'partial') and isinstance(document.get('text'), str)
                 and bool(document['text'].strip()) and not blocked)
        fallback = valid and attempt.get('status') not in ('ok', 'partial') and _preservable_failure(attempt)
        newer = fallback and any(document.get(field) is not None and attempt.get(field) is not None
                                 and document[field] != attempt[field] for field in ('version', 'content_hash'))
        document.update(content_status=document.get('status'), refresh_status=attempt.get('status'),
                        content_usable=bool(valid and (not mismatch or fallback)),
                        cache_status='stale_but_usable' if fallback else 'current' if valid and not mismatch else 'unavailable',
                        cache_blocked=blocked, stale=bool(mismatch and not fallback),
                        newer_version_unavailable=bool(newer))
        document['last_successful_refresh'] = document.get('last_successful_refresh')
        document['last_refresh_error'] = ({key: attempt.get(key) for key in ('error_type', 'http_status', 'attempted_at')}
                                          if attempt.get('status') in ('error', 'unavailable') else None)
        return document

    def load_document(self, course_id, document_key):
        """Read processed text separately from refresh health and cache usability."""
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM documents WHERE namespace = ? AND course_id = ? AND document_key = ?",
                (self.namespace, course_id, document_key),
            ).fetchone()
        return self._decode(row) if row else None

    def load_visual_extraction(self, course_id, content_hash, unit_kind, unit_number, config_hash):
        """Reuse raw page extraction only inside the same account/course/configuration."""
        with self._connection() as connection:
            row = connection.execute(
                "SELECT result_json FROM visual_extractions WHERE namespace=? AND course_id=? "
                "AND content_hash=? AND unit_kind=? AND unit_number=? AND config_hash=?",
                (self.namespace, course_id, content_hash, unit_kind, unit_number, config_hash),
            ).fetchone()
        return json.loads(row[0]) if row else None

    def save_visual_extraction(self, course_id, content_hash, unit_kind, unit_number, config_hash, result):
        """Save each validated page immediately so an interrupted file can resume."""
        from backend.app.vision_service import validate_extraction
        validate_extraction(result["extraction"])
        with self._connection() as connection:
            connection.execute(
                "INSERT OR REPLACE INTO visual_extractions VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (self.namespace, course_id, content_hash, unit_kind, unit_number, config_hash,
                 json.dumps(result), datetime.now(timezone.utc).isoformat()),
            )

    def _documents(self, course_id, *, readable_only=False, served=False, configuration=None):
        snapshot = self.load_course(course_id)
        if not snapshot:
            return []
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM documents WHERE namespace = ? AND course_id = ? ORDER BY document_key",
                (self.namespace, course_id),
            ).fetchall()
        documents = [self._decode(row) for row in rows if visible_in_snapshot(row["last_seen_at"], snapshot)]
        if served:
            documents = self._apply_served_versions(course_id, documents, configuration)
        if readable_only:
            documents = [doc for doc in documents if doc['content_usable']]
        return documents

    def load_documents(self, course_id, *, readable_only=False, served=False, configuration=None):
        """Read documents of the published sync generation (plus uncommitted refresh work).

        Records absent from the published sync remain on disk, but are excluded
        here until rediscovered. Failed refreshes can retain usable cached text;
        retrieval should use readable_only=True for that decision.

        served=False returns each document's latest state (ingestion/indexing).
        served=True is the retrieval view: a changed document keeps its previous
        searchable version until the replacement chunks and embeddings are ready.
        """
        documents = self._documents(course_id, readable_only=readable_only, served=served, configuration=configuration)
        return [{key: value for key, value in doc.items() if key != '_served_chunk_rows'} for doc in documents]

    def load_chunk_state(self, course_id, document_key):
        """Read the saved fingerprint/version, including successful empty sets."""
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM document_chunk_state WHERE namespace = ? AND course_id = ? AND document_key = ?",
                (self.namespace, course_id, document_key),
            ).fetchone()
        return dict(row) if row else None

    def save_chunks(self, course_id, document_key, input_hash, chunker_version, chunks):
        """Replace one document's chunks and completion state in one transaction."""
        rows = [
            (self.namespace, course_id, document_key, chunk["chunk_index"], input_hash,
             chunker_version, chunk["text"], json.dumps(chunk["locations"]))
            for chunk in chunks
        ]
        with self._connection() as connection:
            connection.execute(
                "DELETE FROM document_chunks WHERE namespace = ? AND course_id = ? AND document_key = ?",
                (self.namespace, course_id, document_key),
            )
            connection.executemany("INSERT INTO document_chunks VALUES (?, ?, ?, ?, ?, ?, ?, ?)", rows)
            connection.execute(
                "INSERT OR REPLACE INTO document_chunk_state VALUES (?, ?, ?, ?, ?, ?, ?)",
                (self.namespace, course_id, document_key, input_hash, chunker_version,
                 len(chunks), datetime.now(timezone.utc).isoformat()),
            )

    def load_chunks(self, course_id, document_key=None, *, eligible_only=False, served=False, configuration=None):
        """Return current readable chunks with fresh parent identity and provenance.

        Old chunks may remain on disk after lost access or a failed refresh, but
        never escape this reader. New text/settings require a matching rebuild.
        served=True uses the retrieval view described in load_documents().
        """
        from backend.app import chunking_service

        documents = {
            document["document_key"]: document
            for document in self._documents(course_id, readable_only=True, served=served, configuration=configuration)
            if document_key is None or document["document_key"] == document_key
        }
        fingerprints = {key: chunking_service.chunk_input_hash(doc) for key, doc in documents.items()}
        from backend.app.document_quality_service import document_warning_scope
        scopes = {key: document_warning_scope(doc) for key, doc in documents.items()}
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM document_chunks WHERE namespace = ? AND course_id = ? ORDER BY document_key, chunk_index",
                (self.namespace, course_id),
            ).fetchall()
        retained = {key for key, doc in documents.items() if '_served_chunk_rows' in doc}
        rows = [row for row in rows if row["document_key"] not in retained]
        for key in sorted(retained):
            rows.extend(documents[key].pop('_served_chunk_rows'))
        rows.sort(key=lambda row: (row["document_key"], row["chunk_index"]))
        chunks = []
        for row in rows:
            key = row["document_key"]
            if key not in documents or row["input_hash"] != fingerprints[key] or row["chunker_version"] != chunking_service.CHUNKER_VERSION:
                continue
            document = documents[key]
            chunk = {name: row[name] for name in ("course_id", "document_key", "chunk_index", "input_hash", "chunker_version", "text")}
            chunk.update({name: document.get(name) for name in (
                "canvas_id", "source_type", "title", "url", "format", "updated_at", "created_at",
                "module_id", "module_name", "references", "source_role", "status", "warnings", "extractor_version",
                "content_hash",
                "cache_status", "refresh_status", "last_successful_refresh", "last_refresh_error",
                "newer_version_unavailable", "served_version",
            )})
            chunk["original_filename"] = (document.get("metadata") or {}).get("filename") or document.get("title")
            chunk["locations"] = json.loads(row["locations_json"])
            chunk['document_warning_scope'] = scopes[key]
            chunk.update(chunking_service.chunk_metadata(document, chunk))
            # Future embedding caches must ALSO include model/configuration.
            identity = json.dumps([self.namespace, course_id, chunk["text_hash"]])
            chunk["embedding_reuse_key"] = hashlib.sha256(identity.encode()).hexdigest()
            if eligible_only and not chunk["embedding_eligible"]:
                continue
            chunks.append(chunk)
        return chunks

    def load_embedding_inputs(self, course_id, *, served=False, configuration=None):
        """Group eligible exact text within this account/course; retain every origin.

        This only prepares text/metadata, and never creates or stores embeddings.
        Template quality is retained per source for later policy filtering.
        Indexing uses the latest view; retrieval passes served=True.
        """
        groups = {}
        for chunk in self.load_chunks(course_id, eligible_only=True, served=served, configuration=configuration):
            key = chunk["embedding_reuse_key"]
            group = groups.setdefault(key, {
                "embedding_reuse_key": key, "text_hash": chunk["text_hash"],
                "text": chunk["text"], "sources": [],
            })
            group["sources"].append({name: value for name, value in chunk.items()
                                     if name not in ("text", "text_hash", "embedding_reuse_key")})
        return list(groups.values())

    def save_embeddings(self, course_id, configuration, entries):
        """Commit a validated batch; identities contain no duplicated source text."""
        rows = [
            (self.namespace, course_id, entry["text_hash"], configuration.provider,
             configuration.model, configuration.fingerprint, configuration.json,
             configuration.dimensions, encode_vector(entry["vector"], configuration.dimensions),
             datetime.now(timezone.utc).isoformat())
            for entry in entries
        ]
        with self._connection() as connection:
            connection.executemany(
                "INSERT OR IGNORE INTO chunk_embeddings VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows,
            )

    def load_embeddings(self, course_id, configuration):
        """Read only this account/course/model/configuration's cached vectors."""
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT text_hash, dimensions, vector_blob FROM chunk_embeddings "
                "WHERE namespace = ? AND course_id = ? AND provider = ? AND model = ? AND config_hash = ?",
                (self.namespace, course_id, configuration.provider, configuration.model, configuration.fingerprint),
            ).fetchall()
        vectors = {}
        for row in rows:
            if row["dimensions"] != configuration.dimensions:
                raise ValueError("Stored embedding configuration is inconsistent.")
            vectors[row["text_hash"]] = decode_vector(row["vector_blob"], configuration.dimensions)
        return vectors

    def embedding_dates(self, course_id, configuration):
        with self._connection() as connection:
            rows = connection.execute('SELECT text_hash, created_at FROM chunk_embeddings WHERE namespace=? AND course_id=? AND provider=? AND model=? AND config_hash=?',
                                      (self.namespace, course_id, configuration.provider, configuration.model, configuration.fingerprint)).fetchall()
        return {row[0]: row[1] for row in rows}

    def saved_current_course_ids(self):
        """Use the last saved sync's course list without contacting Canvas."""
        state = self.load_sync_state() or {}
        ids = [course['course_id'] for course in state.get('courses', [])]
        if not ids and state.get('status') in ('queued', 'running', 'interrupted', 'error') and state.get('total_courses') is None:
            ids = state.get('previous_course_ids', [])
        return list(dict.fromkeys(course_id for course_id in ids if self.load_course(course_id) is not None))

    def load_settings(self):
        with self._connection() as connection:
            row = connection.execute('SELECT settings_json FROM app_settings WHERE namespace=?', (self.namespace,)).fetchone()
        return {'auto_index': True, 'monthly_warning_usd': None, 'vision_enabled': None, **(json.loads(row[0]) if row else {})}

    def update_settings(self, changes):
        """Merge preferences atomically so simultaneous controls cannot erase each other."""
        with self._connection() as connection:
            connection.execute('BEGIN IMMEDIATE')
            row = connection.execute('SELECT settings_json FROM app_settings WHERE namespace=?', (self.namespace,)).fetchone()
            settings = {'auto_index': True, 'monthly_warning_usd': None, 'vision_enabled': None, **(json.loads(row[0]) if row else {}), **changes}
            connection.execute('INSERT OR REPLACE INTO app_settings VALUES (?, ?)', (self.namespace, json.dumps(settings)))
        return settings

    def load_index_job(self, course_id):
        with self._connection() as connection:
            row = connection.execute('SELECT state_json FROM search_index_jobs WHERE namespace=? AND course_id=?',
                                     (self.namespace, course_id)).fetchone()
        return json.loads(row[0]) if row else {}

    def save_index_job(self, course_id, state):
        with self._connection() as connection:
            connection.execute('INSERT OR REPLACE INTO search_index_jobs VALUES (?, ?, ?)',
                               (self.namespace, course_id, json.dumps(state)))

    def queue_index_job(self, course_id, configuration, *, manual=False):
        """Coalesce repeated clicks/syncs; failures wait for a bounded retry time."""
        now = datetime.now(timezone.utc).isoformat()
        with self._connection() as connection:
            connection.execute('BEGIN IMMEDIATE')
            row = connection.execute('SELECT state_json FROM search_index_jobs WHERE namespace=? AND course_id=?',
                                     (self.namespace, course_id)).fetchone()
            previous = json.loads(row[0]) if row else {}
            same = previous.get('config_hash') == configuration.fingerprint
            if same and previous.get('status') in ('queued', 'running'):
                return previous
            if same and not manual and (previous.get('retry_after') or '') > now:
                return previous
            state = {**previous, 'run_id': uuid.uuid4().hex, 'status': 'queued',
                     'reason': 'manual' if manual else 'automatic', 'queued_at': now,
                     'configuration': json.loads(configuration.json), 'config_hash': configuration.fingerprint,
                     'error': None, 'retry_after': None, 'failed_hashes': []}
            connection.execute('INSERT OR REPLACE INTO search_index_jobs VALUES (?, ?, ?)',
                               (self.namespace, course_id, json.dumps(state)))
        return state

    def index_jobs(self):
        with self._connection() as connection:
            rows = connection.execute('SELECT course_id, state_json FROM search_index_jobs WHERE namespace=?', (self.namespace,)).fetchall()
        return [(row[0], json.loads(row[1])) for row in rows]

    def record_api_usage(self, entry):
        """Record a provider operation once, without prompts, passages, URLs or credentials."""
        fields = ('operation_id', 'recorded_at', 'operation', 'feature', 'model', 'input_tokens',
                  'output_tokens', 'cached_input_tokens', 'estimated_cost_usd', 'pricing_version')
        with self._connection() as connection:
            connection.execute('INSERT OR IGNORE INTO api_usage VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                               (self.namespace, *(entry[key] for key in fields)))

    def load_api_usage(self):
        with self._connection() as connection:
            rows = connection.execute('SELECT * FROM api_usage WHERE namespace=? ORDER BY recorded_at', (self.namespace,)).fetchall()
        return [{key: row[key] for key in row.keys() if key != 'namespace'} for row in rows]
