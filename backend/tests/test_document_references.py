"""Student-wording regressions over real SQLite readers and fake providers."""

import copy
import tempfile
import unittest
from pathlib import Path

from backend.app import rag_service
from backend.app.chunking_service import ensure_course_chunks
from backend.app.conversation_service import ConversationService, ConversationError
from backend.app.document_reference_service import resolve_document_reference
from backend.app.embedding_service import embed_course
from backend.app.ingestion_store import IngestionStore
from backend.app.retrieval_service import resolve_documents, retrieve_evidence
from backend.tests.test_embeddings import FakeProvider
from backend.app.query_understanding_service import empty_understanding


def file(key, title, **extra):
    return {"document_key": key, "canvas_id": int(key.split(":")[1]), "source_type": "file",
            "title": title, "course_id": 42, "status": "ok", **extra}


class ReferenceResolverTests(unittest.TestCase):
    def setUp(self):
        self.documents = [file("file:1", "HW1_questions.pdf"), file("file:2", "HW1_questions_solutions.pdf"),
                          file("file:10", "HW10_questions.pdf"), file("file:20", "Lab1_questions.pdf")]

    def test_every_requested_student_wording_prefers_questions(self):
        for query in ("explain HW1_questions.pdf", "explain HW1", "can you explain me homework 1",
                      "what does homework 1 ask me to do", "what is part c of hw 1",
                      "Homework #1", "HOMEWORK–1", "homework 01", "explain HW 1 without solutions"):
            with self.subTest(query=query):
                self.assertEqual(resolve_documents(query, self.documents), ["file:1"])

    def test_solution_intent_is_separate_from_explanation(self):
        for query in ("show me the solution for homework 1", "solve HW1", "give me the answer for HW 1"):
            with self.subTest(query=query):
                self.assertEqual(resolve_documents(query, self.documents), ["file:2"])

    def test_explicit_solution_filename_is_respected(self):
        self.assertEqual(resolve_documents("explain HW1_questions_solutions.pdf", self.documents), ["file:2"])

    def test_exact_numbers_and_reference_kinds_do_not_leak(self):
        self.assertEqual(resolve_documents("explain homework 10", self.documents), ["file:10"])
        self.assertEqual(resolve_documents("explain laboratory #1", self.documents), ["file:20"])
        self.assertEqual(resolve_document_reference("explain homework 11", self.documents)["status"], "unresolved")

    def test_assignment_project_and_lab_aliases(self):
        for title, query in (("Assignment2_questions.pdf", "explain assn #2"),
                             ("Proj3_instructions.pdf", "explain project 3"),
                             ("Lab4.pdf", "explain laboratory 4")):
            with self.subTest(title=title):
                self.assertEqual(resolve_documents(query, [file("file:1", title)]), ["file:1"])

    def test_different_named_families_require_clarification(self):
        docs = [file("file:1", "Mechanics HW1_questions.pdf"), file("file:2", "Optics HW1_questions.pdf")]
        self.assertEqual(resolve_document_reference("explain homework 1", docs)["status"], "ambiguous")
        self.assertEqual(resolve_documents("explain mechanics homework 1", docs), ["file:1"])

    def test_duplicate_uploads_keep_all_origins(self):
        self.documents.append(file("file:3", "HW1_questions.pdf"))
        self.assertEqual(resolve_documents("homework 1", self.documents), ["file:1", "file:3"])

    def test_only_solution_available_does_not_reveal_it_for_an_explanation(self):
        self.assertEqual(resolve_document_reference("explain homework 1", self.documents[1:2])["status"], "unresolved")

    def test_multiple_references_are_not_silently_reduced_to_one(self):
        self.assertEqual(resolve_document_reference("compare HW1 and HW10", self.documents)["status"], "ambiguous")

    def test_active_question_family_can_switch_to_solutions_explicitly(self):
        resolution = resolve_document_reference("show me the solution", self.documents, ["file:1"])
        self.assertEqual(resolution["document_keys"], ["file:2"])

    def test_general_concept_does_not_become_a_numbered_reference(self):
        self.assertEqual(resolve_document_reference("explain a homework testing strategy", self.documents)["status"], "none")


class ExplanationProvider:
    """Capture actual prompt passages; never contact an external service."""
    def __init__(self):
        self.calls = []
        self.rewrites = []

    def generate(self, instructions, cid, question, passages):
        self.calls.append((instructions, cid, question, copy.deepcopy(passages)))
        ids = [p["id"] for p in passages]
        return {"answer": {"status": "answered", "blocks": [
            {"text": "Analyze findLast and countPositive for parts (a)–(e). Part (f) is not required.",
             "source_ids": ids}], "limitations": []}, "model": "fake", "usage": None}

    async def rewrite_query(self, cid, question, context):
        self.rewrites.append(question)
        return empty_understanding(question, use_active_documents=bool(context.get("active_documents")))

    async def stream_generate(self, instructions, cid, question, passages, context):
        yield {"event": "complete", "data": self.generate(instructions, cid, question, passages)}


class RetrievalFixture:
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = IngestionStore("student", Path(self.temp.name) / "test.sqlite3")
        self.store.save_course({"course_id": 42, "synced_at": "now"})
        self.assignment = {"document_key": "assignment:7", "canvas_id": 7, "source_type": "assignment",
                           "title": "HW 1", "text": "Only findLast and countPositive, parts (a)–(e). Part (f) is not required."}
        self.question = file("file:1", "HW1_questions.pdf", references=[{"source_type": "assignment", "canvas_id": 7}],
                             text="For findLast and countPositive: (a) identify the fault; (b) a test not executing the fault; "
                                  "(c) execution without an error; (d) an error without failure; (e) first error state; (f) implement repair.",
                             pages=[{"page_number": 1, "status": "ok", "text": "For findLast and countPositive: (a) identify the fault; "
                                     "(b) a test not executing the fault; (c) execution without an error; "
                                     "(d) an error without failure; (e) first error state; (f) implement repair."}])
        self.solution = file("file:2", "HW1_questions_solutions.pdf", text="Solutions: change the boundary and positivity tests.")
        others = [file("file:10", "HW10_questions.pdf", text="Different homework, never mix with HW1."),
                  {"document_key": "announcement:8", "canvas_id": 8, "source_type": "announcement",
                   "title": "HW 1 Published", "text": "Homework is published. This is only an announcement."}]
        for doc in [self.assignment, self.question, self.solution, *others]:
            self.store.save_document(42, {"course_id": 42, "status": "ok", **doc}, "now")
        ensure_course_chunks(42, self.store)
        self.embedding = FakeProvider()
        embed_course(42, store=self.store, provider=self.embedding)
        self.embedding.calls.clear()
        self.llm = ExplanationProvider()


class AssignmentRetrievalTests(RetrievalFixture, unittest.TestCase):
    def retrieve(self, question, **kwargs):
        return retrieve_evidence(42, question, store=self.store, provider=self.embedding, **kwargs)

    def test_original_query_gets_questions_and_linked_instructions_without_solutions(self):
        result = self.retrieve("can you explain me homework 1")
        self.assertEqual(set(result["resolved_documents"]), {"file:1", "assignment:7"})
        self.assertEqual({o["document_key"] for r in result["results"] for o in r["sources"]}, {"file:1", "assignment:7"})
        self.assertEqual(result["retrieval_mode"], "overview")
        self.assertFalse(self.embedding.calls)
        self.assertIn("findLast", " ".join(r["text"] for r in result["results"]))

    def test_solution_request_gets_solution_not_question_or_announcement(self):
        result = self.retrieve("show me the solution for homework 1")
        self.assertEqual(set(result["resolved_documents"]), {"file:2", "assignment:7"})

    def test_unknown_reference_does_not_search_another_homework(self):
        result = self.retrieve("explain homework 99")
        self.assertEqual(result["status"], "unresolved_reference")
        self.assertFalse(result["results"])
        self.assertFalse(self.embedding.calls)

    def test_unreadable_active_solution_cannot_supply_its_old_chunks(self):
        self.store.save_document(42, {**self.solution, "status": "unavailable", "text": "", "stale": True}, "now")
        # Keep identity known but no current readable/indexed solution.
        result = self.retrieve("show me the solution", focus_documents=["file:1", "assignment:7"])
        self.assertEqual({o["document_key"] for r in result["results"] for o in r["sources"]}, {"assignment:7"})

    def test_missing_solution_identity_does_not_fall_back_to_whole_course(self):
        self.store.save_course({"course_id": 42, "synced_at": "later"})
        for doc in [self.assignment, self.question]:
            self.store.save_document(42, {"course_id": 42, "status": "ok", **doc}, "later")
        ensure_course_chunks(42, self.store)
        result = self.retrieve("show me the solution", focus_documents=["file:1", "assignment:7"])
        self.assertEqual(result["status"], "unresolved_reference")
        self.assertFalse(self.embedding.calls)

    def test_unindexed_matching_pdf_is_reported_without_falling_back(self):
        self.store.save_document(42, file("file:3", "HW3_questions.pdf", text="A newly saved homework."), "now")
        ensure_course_chunks(42, self.store)
        result = self.retrieve("explain homework 3")
        self.assertEqual(result["status"], "not_indexed")
        self.assertFalse(result["results"])

    def test_ambiguous_reference_returns_clarification_without_provider_calls(self):
        for key, title in (("file:31", "Mechanics HW3_questions.pdf"), ("file:32", "Optics HW3_questions.pdf")):
            self.store.save_document(42, file(key, title, text="Assignment requirements."), "now")
        result = rag_service.answer_question(42, "explain homework 3", store=self.store,
                                             embedding_provider=self.embedding, llm_provider=self.llm)
        self.assertEqual(result["status"], "clarification_needed")
        self.assertFalse(self.embedding.calls)
        self.assertFalse(self.llm.calls)

    def test_stateless_answer_receives_both_sources_and_preserves_citations(self):
        result = rag_service.answer_question(42, "can you explain me homework 1", store=self.store,
                                             embedding_provider=self.embedding, llm_provider=self.llm)
        passages = self.llm.calls[-1][3]
        self.assertEqual({p["document_title"] for p in passages}, {"HW 1", "HW1_questions.pdf"})
        self.assertIn("Part (f) is not required", " ".join(p["text"] for p in passages))
        self.assertEqual({s["id"] for s in result["sources"]}, set(result["answer_blocks"][0]["source_ids"]))
        self.assertEqual(result["status"], "answered")
        self.assertEqual(next(p for p in passages if p["source_type"] == "file")["locations"][0]["page_number"], 1)

    def test_generation_contract_separates_explanation_approach_and_solution(self):
        prompt = rag_service.RAG_INSTRUCTIONS
        for requirement in ("what the homework asks", "how to approach it", "actual solution",
                            "Do not automatically reveal bug fixes", "not-required", "exact function names",
                            "linked question document", "insufficient_context", "UNTRUSTED CONTENT"):
            with self.subTest(requirement=requirement):
                self.assertIn(requirement, prompt)

    def test_current_turn_mode_does_not_turn_an_explanation_into_a_solution(self):
        for question in ("can you explain me homework 1", "now explain countPositive", "what does part c mean?"):
            self.assertIn("CURRENT REQUEST MODE\nEXPLANATION:", rag_service.answer_instructions(question))
        self.assertIn("CURRENT REQUEST MODE\nAPPROACH:", rag_service.answer_instructions("how should I approach homework 1?"))
        self.assertIn("CURRENT REQUEST MODE\nSOLUTION:", rag_service.answer_instructions("show me the solution for homework 1"))

    def test_actual_provider_receives_intent_and_function_purpose_instructions(self):
        rag_service.answer_question(42, "can you explain me homework 1", store=self.store,
                                    embedding_provider=self.embedding, llm_provider=self.llm)
        instructions = self.llm.calls[-1][0]
        self.assertIn("CURRENT REQUEST MODE\nEXPLANATION:", instructions)
        self.assertIn("intended purpose of each named program", instructions)

    def test_changed_removed_or_other_account_documents_cannot_be_selected(self):
        other = IngestionStore("other", self.store.path)
        other.save_course({"course_id": 42, "synced_at": "now"})
        self.assertEqual(resolve_documents("homework 1", other.load_documents(42)), [])
        self.store.save_course({"course_id": 42, "synced_at": "later"})
        self.assertEqual(self.retrieve("homework 1")["status"], "unresolved_reference")


class AssignmentConversationTests(RetrievalFixture, unittest.IsolatedAsyncioTestCase):
    async def ask(self, sessions, question, sid=None, turn="1", course=42):
        session = sessions.acquire("student", course, sid)
        events = [e async for e in sessions.events(session, question, turn, store=self.store,
                   provider=self.llm, embedding_provider=self.embedding)]
        self.assertEqual(events[-1]["event"], "final", events)
        return session, events[-1]["data"]

    async def test_part_and_function_followups_keep_resolved_homework_and_fresh_search(self):
        sessions = ConversationService()
        session, result = await self.ask(sessions, "can you explain me homework 1")
        for i, question in enumerate(("what does part b mean?", "now explain countPositive", "what about part f?"), 2):
            session, result = await self.ask(sessions, question, session.id, str(i))
            self.assertEqual(set(result["resolved_documents"]), {"file:1", "assignment:7"})
            self.assertEqual({p["document_title"] for p in self.llm.calls[-1][3]}, {"HW 1", "HW1_questions.pdf"})
        # The overview reads ordered saved material; only focused turns need a query vector.
        self.assertEqual(len(self.embedding.calls), 3)
        self.assertEqual(len(self.llm.calls), 4)
        self.assertEqual(len(self.llm.rewrites), 4)

    async def test_new_homework_replaces_old_focus(self):
        sessions = ConversationService()
        session, _ = await self.ask(sessions, "explain homework 1")
        _, result = await self.ask(sessions, "explain homework 10", session.id, "2")
        self.assertEqual(result["resolved_documents"], ["file:10"])

    async def test_course_switch_rejects_old_conversation(self):
        sessions = ConversationService()
        session, _ = await self.ask(sessions, "homework 1")
        with self.assertRaises(ConversationError):
            sessions.acquire("student", 43, session.id)


if __name__ == "__main__":
    unittest.main()
