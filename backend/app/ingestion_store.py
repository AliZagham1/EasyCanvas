"""Local development storage; no Canvas requests or document parsing live here."""

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_DATABASE = Path(__file__).resolve().parents[1] / "data" / "easycanvas.sqlite3"
TEXT_COLLECTIONS = ("pages", "slides", "blocks")


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
            }
            connection.execute("INSERT OR REPLACE INTO course_sync VALUES (?, ?)", (self.namespace, json.dumps(state)))
        return state

    def save_document(self, course_id, document, seen_at):
        """Save one attempt without erasing previously readable text on failure."""
        attempt = {key: value for key, value in document.items() if key not in ("text", *TEXT_COLLECTIONS)}
        attempt["attempted_at"] = datetime.now(timezone.utc).isoformat()
        payload = dict(document)
        previous = self.load_document(course_id, document["document_key"])
        if document["status"] not in ("ok", "partial") and previous and previous.get("text"):
            payload = {key: value for key, value in previous.items() if key not in ("latest_attempt", "stale")}
        # Store text once in its original units, then join it again on read.
        if any(payload.get(key) for key in TEXT_COLLECTIONS):
            payload.pop("text", None)
        with self._connection() as connection:
            connection.execute(
                "INSERT OR REPLACE INTO documents VALUES (?, ?, ?, ?, ?, ?)",
                (self.namespace, course_id, document["document_key"], json.dumps(payload), json.dumps(attempt), seen_at),
            )

    @staticmethod
    def _decode(row):
        document = json.loads(row["document_json"])
        for key in TEXT_COLLECTIONS:
            if document.get(key):
                document["text"] = "\n\n".join(unit["text"] for unit in document[key] if unit.get("text"))
                break
        attempt = json.loads(row["attempt_json"])
        document["latest_attempt"] = attempt
        document["stale"] = document.get("status") != attempt.get("status") or document.get("version") != attempt.get("version")
        return document

    def load_document(self, course_id, document_key):
        """Read cached text plus the latest attempt; stale text is explicitly flagged."""
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM documents WHERE namespace = ? AND course_id = ? AND document_key = ?",
                (self.namespace, course_id, document_key),
            ).fetchone()
        return self._decode(row) if row else None

    def load_documents(self, course_id, *, readable_only=False):
        """Read documents seen in the latest sync, never silently resurrect removed ones.

        Records absent during a failed listing remain on disk, but are excluded
        here until rediscovered. Future retrieval should use readable_only=True.
        """
        snapshot = self.load_course(course_id)
        if not snapshot:
            return []
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM documents WHERE namespace = ? AND course_id = ? AND last_seen_at = ? ORDER BY document_key",
                (self.namespace, course_id, snapshot["synced_at"]),
            ).fetchall()
        documents = [self._decode(row) for row in rows]
        if readable_only:
            documents = [doc for doc in documents if not doc["stale"] and doc["status"] in ("ok", "partial") and doc.get("text")]
        return documents
