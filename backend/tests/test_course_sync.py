import asyncio
import json
import signal
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import requests
from filelock import FileLock

from backend.app import canvas_service, course_sync_service, ingestion_service
from backend.app.ingestion_store import IngestionStore


def course(course_id):
    return {"id": course_id, "course_code": f"TEST {course_id}", "name": "Test course", "enrollment_term_id": 9}


class CurrentCoursesTests(unittest.TestCase):
    def test_all_unique_current_courses_are_ingested_and_a_failure_does_not_stop_others(self):
        store = Mock()
        def ingest(course_id, **kwargs):
            if course_id == 2:
                raise requests.HTTPError("URL must never be exposed", response=Mock(status_code=403))
            return {"snapshot": {"coverage": "partial" if course_id == 3 else "ok"}, "statistics": {"readable": 4}}
        updates = []
        with patch.object(canvas_service, "get_current_courses", return_value=[course(1), course(2), course(1), course(3)]), patch.object(ingestion_service, "ingest_course", side_effect=ingest) as run:
            report = ingestion_service.ingest_current_courses(store=store, on_update=updates.append)
        self.assertEqual([call.args[0] for call in run.call_args_list], [1, 2, 3])
        self.assertEqual(report["completed_courses"], 2)
        self.assertEqual(report["failed_courses"], 1)
        self.assertEqual(report["coverage"], "partial")
        self.assertEqual([item["status"] for item in updates[0]], ["pending"] * 3)
        self.assertEqual(report["courses"][1]["http_status"], 403)
        self.assertNotIn("URL must never", json.dumps(report))

    def test_new_semester_courses_are_fetched_again_without_remembering_previous_ids(self):
        result = {"snapshot": {"coverage": "ok"}, "statistics": {}}
        with patch.object(canvas_service, "get_current_courses", side_effect=[[course(1)], [course(8), course(9)]]), patch.object(ingestion_service, "ingest_course", return_value=result) as ingest:
            ingestion_service.ingest_current_courses(store=Mock())
            ingestion_service.ingest_current_courses(store=Mock())
        self.assertEqual([call.args[0] for call in ingest.call_args_list], [1, 8, 9])

    def test_empty_courses_do_not_fetch_documents(self):
        with patch.object(canvas_service, "get_current_courses", return_value=[]), patch.object(ingestion_service, "ingest_course") as ingest:
            report = ingestion_service.ingest_current_courses(store=Mock())
        ingest.assert_not_called()
        self.assertEqual(report["total_courses"], 0)

    def test_empty_course_list_does_not_crash_current_course_selection(self):
        with patch.object(canvas_service, "_get_student_courses", return_value=[]):
            self.assertEqual(canvas_service.get_current_courses(), [])


class BackgroundSyncTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.store = IngestionStore("test-student", Path(self.directory.name) / "ingestion.sqlite3")

    def test_duplicate_requests_reuse_a_persistent_job_and_accounts_are_isolated(self):
        first = self.store.queue_sync()
        self.assertEqual(first["run_id"], self.store.queue_sync()["run_id"])
        reopened = IngestionStore("test-student", self.store.path)
        self.assertEqual(reopened.load_sync_state(), first)
        other = IngestionStore("other-student", self.store.path)
        self.assertIsNone(other.load_sync_state())
        self.store.save_sync_state({**first, "status": "completed"})
        self.assertNotEqual(first["run_id"], self.store.queue_sync()["run_id"])

    def test_worker_updates_course_progress_and_finishes_without_raw_course_data(self):
        state = self.store.queue_sync()
        courses = [{"course_id": 1, "status": "completed", "coverage": "partial", "statistics": {"readable": 3}}]
        def ingest(**kwargs):
            kwargs["on_update"](courses)
            return {"courses": courses, "total_courses": 1, "completed_courses": 1, "failed_courses": 0, "coverage": "partial"}
        with patch.object(course_sync_service, "ingest_current_courses", side_effect=ingest):
            course_sync_service._run_sync(self.store, state, 1800)
        loaded = self.store.load_sync_state()
        self.assertEqual(loaded["status"], "completed")
        self.assertEqual(loaded["courses"][0]["statistics"]["readable"], 3)
        self.assertIsNotNone(loaded["next_sync_at"])

    def test_course_discovery_failure_is_safe_and_scheduled_for_retry(self):
        state = self.store.queue_sync()
        with patch.object(course_sync_service, "ingest_current_courses", side_effect=requests.ConnectionError("private signed URL")):
            course_sync_service._run_sync(self.store, state, 1800)
        loaded = self.store.load_sync_state()
        self.assertEqual(loaded["status"], "error")
        self.assertNotIn("private signed URL", json.dumps(loaded))
        self.assertIsNotNone(loaded["next_sync_at"])

    def test_startup_runs_once_then_periodic_schedule_runs_again(self):
        sleeps = 0
        def sleep(_seconds):
            nonlocal sleeps
            sleeps += 1
            if sleeps == 1:
                state = self.store.load_sync_state()
                state["next_sync_at"] = "2000-01-01T00:00:00+00:00"
                self.store.save_sync_state(state)
            if sleeps == 3:
                raise KeyboardInterrupt
        report = {"courses": [], "total_courses": 0, "completed_courses": 0, "failed_courses": 0, "coverage": "ok"}
        with patch.object(course_sync_service, "ingest_current_courses", return_value=report) as ingest, patch.object(course_sync_service.time, "sleep", side_effect=sleep):
            course_sync_service.run_worker(self.store, 60)
        self.assertEqual(ingest.call_count, 2)
        self.assertEqual(self.store.load_sync_state()["reason"], "scheduled")

    def test_interruption_is_recorded_and_restart_recovers_the_job(self):
        def interrupt(**kwargs):
            kwargs["on_update"]([{"course_id": 1, "status": "running"}])
            raise KeyboardInterrupt
        with patch.object(course_sync_service, "ingest_current_courses", side_effect=interrupt):
            course_sync_service.run_worker(self.store, 60)
        state = self.store.load_sync_state()
        self.assertEqual(state["status"], "interrupted")
        self.assertEqual(state["courses"][0]["status"], "interrupted")
        old_id = state["run_id"]
        with patch.object(course_sync_service, "ingest_current_courses", return_value={"courses": [], "total_courses": 0, "completed_courses": 0, "failed_courses": 0, "coverage": "ok"}), patch.object(course_sync_service.time, "sleep", side_effect=KeyboardInterrupt):
            course_sync_service.run_worker(self.store, 60)
        self.assertNotEqual(old_id, self.store.load_sync_state()["run_id"])

    def test_another_worker_holding_the_lock_prevents_duplicate_sweeps(self):
        path = self.store.path.with_name(".course-sync-test-student.lock")
        with FileLock(path, timeout=0):
            with patch.object(course_sync_service, "ingest_current_courses") as ingest:
                course_sync_service.run_worker(self.store, 60)
            ingest.assert_not_called()

    def test_service_reuses_its_worker_and_shutdown_interrupts_only_that_process(self):
        process = Mock()
        process.poll.return_value = None
        with patch.object(course_sync_service.subprocess, "Popen", return_value=process) as spawn:
            service = course_sync_service.CourseSyncService(self.store, interval=60)
            service.start()
            first = service.request_sync()
            second = service.request_sync()
            self.assertEqual(first["run_id"], second["run_id"])
            spawn.assert_called_once()
            service.stop()
        process.send_signal.assert_called_once_with(signal.CTRL_BREAK_EVENT if course_sync_service.IS_WINDOWS else signal.SIGINT)
        process.wait.assert_called_once_with(timeout=5)

    def test_a_crashed_worker_is_reported_as_an_error_instead_of_running_forever(self):
        self.store.queue_sync()
        service = course_sync_service.CourseSyncService(self.store, interval=60)
        service._process = Mock()
        service._process.poll.return_value = -9
        self.assertEqual(service.status()["status"], "error")
        self.assertEqual(self.store.load_sync_state()["error_type"], "CourseSyncWorkerExited")

    def test_windows_worker_uses_a_console_group_and_break_signal(self):
        process = Mock()
        process.poll.return_value = None
        with patch.object(course_sync_service, "IS_WINDOWS", True), \
             patch.object(course_sync_service.subprocess, "CREATE_NEW_PROCESS_GROUP", 512, create=True), \
             patch.object(signal, "CTRL_BREAK_EVENT", 1, create=True), \
             patch.object(course_sync_service.subprocess, "Popen", return_value=process) as spawn:
            service = course_sync_service.CourseSyncService(self.store, interval=60)
            service.start()
            service.stop()
        self.assertEqual(spawn.call_args.kwargs["creationflags"], 512)
        self.assertNotIn("start_new_session", spawn.call_args.kwargs)
        process.send_signal.assert_called_once_with(1)

    def test_windows_shutdown_timeout_kills_our_worker_without_unix_calls(self):
        process = Mock()
        process.poll.return_value = None
        process.wait.side_effect = [course_sync_service.subprocess.TimeoutExpired("worker", 5), 0]
        with patch.object(course_sync_service, "IS_WINDOWS", True), \
             patch.object(signal, "CTRL_BREAK_EVENT", 1, create=True), \
             patch.object(course_sync_service.os, "killpg", create=True) as kill_group:
            service = course_sync_service.CourseSyncService(self.store, interval=60)
            service._process = process
            service.stop()
        process.kill.assert_called_once()
        kill_group.assert_not_called()

    def test_worker_releases_its_lock_after_an_unexpected_failure(self):
        with patch.object(course_sync_service, "_run_sync", side_effect=RuntimeError("test")):
            with self.assertRaises(RuntimeError):
                course_sync_service.run_worker(self.store, 60)
        path = self.store.path.with_name(".course-sync-test-student.lock")
        with FileLock(path, timeout=0):
            pass

    def test_worker_exits_when_its_backend_parent_is_gone(self):
        with patch.object(course_sync_service.os, "getppid", return_value=2), patch.object(course_sync_service, "ingest_current_courses") as ingest:
            course_sync_service.run_worker(self.store, 60, parent_pid=1)
        ingest.assert_not_called()
        self.assertEqual(self.store.load_sync_state()["status"], "interrupted")


class SyncApiTests(unittest.TestCase):
    @staticmethod
    async def request(app, method, path):
        messages = []
        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}
        async def send(message):
            messages.append(message)
        await app({"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": method, "scheme": "http", "path": path, "raw_path": path.encode(), "query_string": b"", "root_path": "", "headers": [], "server": ("test", 80), "client": ("test", 1)}, receive, send)
        code = next(message["status"] for message in messages if message["type"] == "http.response.start")
        body = b"".join(message.get("body", b"") for message in messages if message["type"] == "http.response.body")
        return code, json.loads(body)

    def test_lifespan_starts_background_service_and_api_returns_immediately(self):
        from backend.app import main
        service = Mock()
        service.status.return_value = {"status": "running", "total_courses": 2}
        service.request_sync.return_value = {"status": "queued", "run_id": "test-run"}
        async def run():
            async with main.app.router.lifespan_context(main.app):
                self.assertEqual((await self.request(main.app, "GET", "/health"))[0], 200)
                self.assertEqual(await self.request(main.app, "GET", "/api/ingestion"), (200, service.status.return_value))
                self.assertEqual(await self.request(main.app, "POST", "/api/ingestion/sync"), (202, service.request_sync.return_value))
        with patch.object(main, "CourseSyncService", return_value=service):
            asyncio.run(run())
        service.start.assert_called_once()
        service.stop.assert_called_once()

    def test_missing_config_does_not_break_health_and_sync_has_recovery_error(self):
        from backend.app import main
        async def run():
            async with main.app.router.lifespan_context(main.app):
                self.assertEqual((await self.request(main.app, "GET", "/health"))[0], 200)
                self.assertEqual((await self.request(main.app, "GET", "/api/ingestion"))[1]["status"], "error")
                self.assertEqual((await self.request(main.app, "POST", "/api/ingestion/sync"))[0], 503)
        with patch.object(main, "CourseSyncService", side_effect=ValueError("missing config")):
            asyncio.run(run())
