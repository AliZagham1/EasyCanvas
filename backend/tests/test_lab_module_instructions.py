"""EC-018: a solve request for an assignment whose own record is empty must still read its module's instruction file."""

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from backend.app.assignment_policy_service import prohibited_request
from backend.app.chunking_service import ensure_course_chunks
from backend.app.embedding_service import embed_course
from backend.app.ingestion_store import IngestionStore
from backend.app.query_understanding_service import empty_understanding
from backend.app.retrieval_router import route_query
from backend.tests.test_embeddings import FakeProvider

NOW = datetime(2026, 11, 1, 18, tzinfo=timezone.utc)


def module(mid, name):
    return {"canvas_id": mid, "course_id": 7, "source_type": "module", "title": name, "metadata": {"id": mid, "name": name}}


def empty_assignment(aid, title):
    return {"canvas_id": aid, "course_id": 7, "source_type": "assignment", "title": title, "metadata": {"id": aid, "name": title, "description": ""}}


class LabModuleInstructionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.store = IngestionStore("student", Path(self.temp.name) / "lab.sqlite3")
        self.snapshot = {"course_id": 7, "synced_at": "2026-11-01T12:00:00Z",
                         "course": {"metadata": {"course_code": "CSE 0000", "name": "Systems"}},
                         "modules": [module(11, "LAB 3"), module(12, "Lab 4")],
                         "assignments": [empty_assignment(31, "Lab 3"), empty_assignment(41, "Lab 4")]}
        self.store.save_course(self.snapshot)
        for a in self.snapshot["assignments"]:
            self.store.save_document(7, {"document_key": f"assignment:{a['canvas_id']}", "canvas_id": a["canvas_id"], "course_id": 7,
                "title": a["title"], "source_type": "assignment", "status": "no_text", "text": ""}, self.snapshot["synced_at"])
        for fid, title, mid, mname, text in ((901, "Lab 3.docx", 11, "LAB 3", "Programming Assignment 3: write a small shell with a prompt and built-in commands."),
                                             (902, "Prog4.doc", 12, "Lab 4", "Programming Assignment 4: store and share files securely.")):
            self.store.save_document(7, {"document_key": f"file:{fid}", "canvas_id": fid, "course_id": 7, "title": title,
                "source_type": "file", "status": "ok", "text": text,
                "references": [{"source_type": "module_item", "canvas_id": fid + 1000, "title": title, "module_id": mid, "module_name": mname}]},
                self.snapshot["synced_at"])
        ensure_course_chunks(7, self.store)
        self.provider = FakeProvider(); embed_course(7, store=self.store, provider=self.provider)

    def route(self, question, intent, reference):
        plan = empty_understanding(question, intent=intent, assignment_reference=reference)
        return route_query(7, question, plan, store=self.store, provider=self.provider, now=NOW)

    def keys(self, result):
        return {o["document_key"] for r in result["results"] for o in r["sources"]}

    def test_solve_request_finds_module_instruction_file(self):
        for question, reference, expected in (("can you solve lab 3", "Lab 3", "file:901"), ("can you solve lab 4", "Lab 4", "file:902")):
            with self.subTest(reference=reference):
                result = self.route(question, "solve_question", reference)
                self.assertEqual(result["retrieval_route"], "assignment_material")
                self.assertNotEqual(result["status"], "no_content")
                self.assertIn(expected, self.keys(result))

    def test_solve_request_only_reads_its_own_labs_file(self):
        self.assertNotIn("file:902", self.keys(self.route("can you solve lab 3", "solve_question", "Lab 3")))

    def test_explain_request_behaviour_is_unchanged(self):
        self.assertIn("file:901", self.keys(self.route("explain lab 3", "explain_assignment", "Lab 3")))

    def test_ai_ban_written_in_the_lab_file_applies_to_a_solve_request(self):
        self.store.save_document(7, {"document_key": "file:901", "canvas_id": 901, "course_id": 7, "title": "Lab 3.docx",
            "source_type": "file", "status": "ok",
            "text": "Programming Assignment 3: write a small shell. You are not allowed to use AI tools on this assignment.",
            "references": [{"source_type": "module_item", "canvas_id": 1901, "title": "Lab 3.docx", "module_id": 11, "module_name": "LAB 3"}]},
            self.snapshot["synced_at"])
        ensure_course_chunks(7, self.store); embed_course(7, store=self.store, provider=self.provider)
        question = "can you solve lab 3"
        result = self.route(question, "solve_question", "Lab 3")
        plan = result["query_understanding"]
        self.assertTrue(prohibited_request(question, plan, result.get("assignment_policy")))


if __name__ == "__main__":
    unittest.main()
