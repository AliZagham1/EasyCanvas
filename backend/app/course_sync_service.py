"""Automatic current-course ingestion, isolated from the FastAPI web process."""

import argparse
import os
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from filelock import FileLock, Timeout

from backend.app.canvas_service import get_cache_namespace
from backend.app.ingestion_service import ingest_current_courses
from backend.app.ingestion_store import DEFAULT_DATABASE, IngestionStore

DEFAULT_INTERVAL_SECONDS = 30 * 60
PROJECT_ROOT = Path(__file__).resolve().parents[2]
IS_WINDOWS = os.name == "nt"


def _now():
    return datetime.now(timezone.utc)


def _run_sync(store, state, interval, parent_pid=None):
    """Persist progress for one sweep; store only safe counters and error categories."""
    state.update(status="running", started_at=_now().isoformat())
    store.save_sync_state(state)

    def update(courses):
        if parent_pid is not None and os.getppid() != parent_pid:
            raise KeyboardInterrupt
        state.update(
            courses=courses, total_courses=len(courses),
            current_course_id=next((course["course_id"] for course in courses if course["status"] == "running"), None),
            completed_courses=sum(course["status"] == "completed" for course in courses),
            failed_courses=sum(course["status"] == "error" for course in courses),
        )
        store.save_sync_state(state)

    try:
        report = ingest_current_courses(store=store, on_update=update)
        state.update(report, status="completed", current_course_id=None)
    except Exception as error:
        code = error.response.status_code if isinstance(error, requests.RequestException) and error.response is not None else None
        state.update(status="error", error_type=type(error).__name__, http_status=code, coverage="unavailable", current_course_id=None)
    state.update(finished_at=_now().isoformat(), next_sync_at=(_now() + timedelta(seconds=interval)).isoformat())
    store.save_sync_state(state)


def run_worker(store, interval, parent_pid=None):
    """Run on startup, on queued requests, and periodically while the backend lives.

    The OS lock prevents two web processes from starting duplicate workers for
    the same local account/database. A fresh worker recovers interrupted jobs.
    """
    lock_path = store.path.with_name(f".course-sync-{store.namespace}.lock")
    lock = FileLock(lock_path, mode=0o600)
    try:
        lock.acquire(timeout=0)
    except Timeout:
        return
    try:
        try:
            state = store.load_sync_state()
            if not state or state["status"] != "queued":
                store.queue_sync("startup", reset=True)
            while True:
                if parent_pid is not None and os.getppid() != parent_pid:
                    raise KeyboardInterrupt
                state = store.load_sync_state()
                if state["status"] == "queued":
                    _run_sync(store, state, interval, parent_pid)
                elif state.get("next_sync_at") and _now() >= datetime.fromisoformat(state["next_sync_at"]):
                    store.queue_sync("scheduled")
                time.sleep(1)
        except KeyboardInterrupt:
            state = store.load_sync_state()
            if state and state["status"] in ("queued", "running"):
                for course in state["courses"]:
                    if course["status"] == "running":
                        course["status"] = "interrupted"
                state.update(status="interrupted", current_course_id=None, finished_at=_now().isoformat(), next_sync_at=None)
                store.save_sync_state(state)
    finally:
        lock.release()


class CourseSyncService:
    """Own a background worker and expose its persistent state to API routes."""

    def __init__(self, store=None, interval=None):
        self.store = store or IngestionStore(get_cache_namespace())
        self.interval = int(interval if interval is not None else os.getenv("COURSE_SYNC_INTERVAL_SECONDS", DEFAULT_INTERVAL_SECONDS))
        if self.interval < 60:
            raise ValueError("COURSE_SYNC_INTERVAL_SECONDS must be at least 60.")
        self._process = None
        self._lock = threading.Lock()

    def _start_process(self):
        if self._process is None or self._process.poll() is not None:
            # Windows console groups accept CTRL_BREAK; POSIX uses a new session.
            options = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if IS_WINDOWS else {"start_new_session": True}
            self._process = subprocess.Popen(
                [sys.executable, "-m", "backend.app.course_sync_service", "--worker", "--database", str(self.store.path), "--interval", str(self.interval), "--parent-pid", str(os.getpid())],
                cwd=PROJECT_ROOT, stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                **options,
            )

    def start(self):
        with self._lock:
            self.store.queue_sync("startup")
            self._start_process()

    def request_sync(self):
        """Return immediately; repeated clicks reuse the active job."""
        with self._lock:
            state = self.store.queue_sync("manual")
            self._start_process()
            return state

    def status(self):
        state = self.store.load_sync_state() or {"status": "idle", "courses": [], "total_courses": None}
        if self._process is not None and self._process.poll() not in (None, 0) and state["status"] in ("queued", "running"):
            state.update(status="error", error_type="CourseSyncWorkerExited", finished_at=_now().isoformat(), next_sync_at=None)
            self.store.save_sync_state(state)
        state["interval_seconds"] = self.interval
        return state

    def stop(self):
        """Interrupt our worker so its converter/temp-file cleanup can run."""
        with self._lock:
            process = self._process
            if process is None or process.poll() is not None:
                return
            try:
                process.send_signal(signal.CTRL_BREAK_EVENT if IS_WINDOWS else signal.SIGINT)
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                if IS_WINDOWS:
                    process.kill()
                else:
                    os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=2)
            except ProcessLookupError:
                pass


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worker", action="store_true", required=True)
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    parser.add_argument("--interval", type=int, default=DEFAULT_INTERVAL_SECONDS)
    parser.add_argument("--parent-pid", type=int)
    args = parser.parse_args()
    if args.interval < 60:
        parser.error("--interval must be at least 60 seconds")

    def interrupted(signum, frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, interrupted)
    if IS_WINDOWS:
        signal.signal(signal.SIGBREAK, interrupted)
    run_worker(IngestionStore(get_cache_namespace(), args.database), args.interval, args.parent_pid)
