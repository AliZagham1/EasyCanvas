"""Page-quality, ordered overview, continuation and warning regression tests."""

import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.app import rag_service, retrieval_service
from backend.app.chunking_service import ensure_course_chunks
from backend.app.document_quality_service import (
    assess_document_quality, reassess_saved_course, UNREADABLE_MESSAGE, OVERVIEW_MESSAGE,
)
from backend.app.embedding_service import embed_course
from backend.app.ingestion_store import IngestionStore
from backend.app.retrieval_context import select_overview, overview_requested, select_numbered_section
from backend.tests.test_embeddings import FakeProvider
from backend.tests.test_rag import FakeLLM


def page(number, text, **changes):
    return {"page_number": number, "text": text, "status": "ok", **changes}


def document(pages, **changes):
    return {"course_id": 42, "document_key": "file:10", "canvas_id": 10,
            "title": "Practice Questions 10.pdf", "source_type": "file", "status": "ok",
            "text": "\n\n".join(p["text"] for p in pages), "pages": pages, **changes}


def boundary_document():
    return document([
        page(1, "Question 7\nThis is called normalizing \\(\\mathbf{u}\\):", status="partial",
             extraction_method="vision", extraction_status="partial", warnings=["visual_content_incomplete"],
             unreadable=["The page ends after “This is called normalizing **u**:”; any continuation is not visible."]),
        page(2, "\\frac{u}{\\|u\\|} gives a unit vector.\nThe solution continues here."),
    ], status="partial")


def item(number, text, **location):
    return {"text_hash": str(number), "text": text, "sources": [{"document_key": "file:10", "course_id": 42,
            "source_type": "file", "title": "Practice Questions 10.pdf", "chunk_index": number,
            "locations": [{"kind": "pdf_page", "page_number": number + 1, **location}],
            "embedding_eligible": True, "status": "ok", "warnings": [],
            "source_quality": {"policy_retrieval_eligible": True}}]}


class PageQualityTests(unittest.TestCase):
    def test_natural_continuation_resolves_only_the_boundary_warning(self):
        original = boundary_document()
        result = assess_document_quality(original)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["pages"][0]["warnings"], [])
        self.assertEqual(result["pages"][0]["unreadable"], [])
        self.assertEqual(result["pages"][0]["continues_on"], 2)
        self.assertEqual(result["pages"][1]["continues_from"], 1)
        self.assertTrue(result["pages"][0]["resolved_continuation_notes"])
        self.assertEqual(original["status"], "partial")
        self.assertEqual(result["text"], original["text"])

    def test_reassessment_is_idempotent(self):
        first = assess_document_quality(boundary_document())
        self.assertEqual(assess_document_quality(first), first)

    def test_explicit_page_break_notes_are_not_unreadable_regions(self):
        for note in ("The explanation continues on the next page.", "The sentence is split by a page break."):
            doc = boundary_document();doc["pages"][0]["unreadable"] = [note]
            self.assertEqual(assess_document_quality(doc)["status"], "ok")

    def test_genuine_unreadability_is_not_cleared_by_the_words_page_break(self):
        doc = boundary_document();doc["pages"][0]["unreadable"] = ["An unreadable formula lies near the page break."]
        self.assertEqual(assess_document_quality(doc)["status"], "partial")

    def test_normal_continuation_without_any_failure_adds_no_warning(self):
        result = assess_document_quality(document([page(1, "The projection is"), page(2, "\\hat y = 4u.")]))
        self.assertEqual(result["status"], "ok")
        self.assertFalse(result["pages"][0].get("warnings"))
        self.assertEqual(result["pages"][0]["continues_on"], 2)

    def test_real_unreadable_region_is_preserved_alongside_a_continuation(self):
        original = boundary_document()
        original["pages"][0]["unreadable"].append("A blurred matrix entry is unreadable.")
        result = assess_document_quality(original)
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["pages"][0]["unreadable"], ["A blurred matrix entry is unreadable."])
        self.assertIn("visual_content_incomplete", result["pages"][0]["warnings"])

    def test_final_page_without_a_successor_stays_partial(self):
        doc = boundary_document();doc["pages"] = doc["pages"][:1]
        self.assertEqual(assess_document_quality(doc)["status"], "partial")

    def test_new_question_is_not_mistaken_for_a_continuation(self):
        doc = boundary_document();doc["pages"][1]["text"] = "Question 8\nA new exercise starts here."
        result = assess_document_quality(doc)
        self.assertEqual(result["status"], "partial")
        self.assertNotIn("continues_on", result["pages"][0])

    def test_failed_successor_and_missing_page_cannot_prove_continuation(self):
        for update in ({"status": "error"}, {"page_number": 3}):
            doc = boundary_document();doc["pages"][1].update(update)
            self.assertEqual(assess_document_quality(doc)["status"], "partial")

    def test_unrelated_document_failure_is_not_erased(self):
        doc = boundary_document();doc["warnings"] = ["native_content_not_preserved"]
        self.assertEqual(assess_document_quality(doc)["status"], "partial")

    def test_unknown_partial_reason_is_not_silently_upgraded(self):
        doc = document([page(1, "Ordinary complete content.")], status="partial")
        self.assertEqual(assess_document_quality(doc)["status"], "partial")

    def test_genuine_page_failure_makes_an_inconsistent_ok_document_partial(self):
        doc = document([page(1, "Readable fragment.", status="partial", unreadable=["A matrix value is blurred."])])
        self.assertEqual(assess_document_quality(doc)["status"], "partial")


class OverviewSelectionTests(unittest.TestCase):
    def test_numbered_section_beats_summary_and_keeps_numbered_next_page(self):
        entries = [item(0, 'Earlier material.\nQuestion 2. Ordering language\n1. Every order starts with a keyword.'),
                   item(1, '2. Include an item.\n3. End with a semicolon.\nb. Convert the grammar to EBNF.'),
                   item(2, 'Continue part b: use brackets for optional components.'),
                   item(3, 'Question 3. A different exercise begins.'),
                   item(4, 'Question 2. Ordering language\nQuestion 3. Another question\nQuestion 4. A third question')]
        chosen = select_numbered_section('Explain question 2 part b', list(reversed(entries)))
        self.assertEqual([entry['sources'][0]['chunk_index'] for entry in chosen], [0, 1, 2])
        self.assertIn('Convert the grammar', chosen[1]['text'])

    def test_numbered_section_supports_other_headings_and_budget(self):
        for kind, number in (('Problem', '4'), ('Section', '3'), ('Part', 'B'), ('Requirement', '2')):
            with self.subTest(kind=kind):
                entries = [item(0, f'{kind} {number}. Important material for the student.'),
                           item(1, 'The same section continues on this next page.'),
                           item(2, 'A final continuation before the next heading.'),
                           item(3, f'{kind} 9. A different section.')]
                selected = select_numbered_section(f'Explain {kind} {number}', entries, max_passages=2)
                self.assertEqual([entry['text_hash'] for entry in selected], ['0', '1'])

    def test_section_never_crosses_a_document_or_missing_chunk(self):
        entries = [item(0, 'Question 2. Explain this task.'), item(2, 'Another chunk after a gap.')]
        entries.append(item(1, 'A different document.'))
        entries[-1]['sources'][0]['document_key'] = 'file:other'
        selected = select_numbered_section('Explain question 2', entries)
        self.assertEqual([entry['text_hash'] for entry in selected], ['0'])

    def test_section_trims_an_unrelated_question_on_same_page(self):
        entries = [item(0, 'Previous question ends.\nQuestion 2. Start here.'),
                   item(1, 'Part b continues here.\nQuestion 3. Do not include this.')]
        selected = select_numbered_section('Explain question 2 part b', entries)
        self.assertEqual(len(selected), 2)
        self.assertNotIn('Previous question', selected[0]['text'])
        self.assertIn('Part b continues', selected[1]['text'])
        self.assertNotIn('Question 3', selected[1]['text'])
        self.assertEqual(selected[1]['source_text_hash'], '1')

    def test_short_document_uses_every_chunk_in_source_order(self):
        entries = [item(n, f"Question {n}: a complete independent exercise.") for n in range(6)]
        selected = select_overview(list(reversed(entries)))
        self.assertEqual([entry["text_hash"] for entry in selected], [str(n) for n in range(6)])

    def test_long_document_uses_broad_coverage_within_both_limits(self):
        entries = [item(n, f"Section {n}. " + "Material for this section. " * 50) for n in range(60)]
        selected = select_overview(entries)
        positions = [entry["sources"][0]["chunk_index"] for entry in selected]
        self.assertLessEqual(len(selected), 24)
        self.assertLessEqual(sum(len(entry["text"]) for entry in selected), 32000)
        self.assertEqual(positions, sorted(positions))
        self.assertIn(0, positions)
        self.assertIn(59, positions)
        self.assertTrue(any(25 <= position <= 35 for position in positions))

    def test_character_limit_is_independent_of_passage_count(self):
        entries = [item(n, f"Section {n}. " + "Substantive material. " * 80) for n in range(20)]
        selected = select_overview(entries, max_characters=5000)
        self.assertLess(len(selected), len(entries))
        self.assertLessEqual(sum(len(entry["text"]) for entry in selected), 5000)

    def test_continuation_pair_is_selected_as_a_bundle(self):
        entries = [item(0, "The projection is", continues_on=2), item(1, "\\hat y = 4u.", continues_from=1),
                   item(2, "Question 8. A different exercise.")]
        selected = select_overview(entries, max_passages=2)
        self.assertEqual([entry["text_hash"] for entry in selected], ["0", "1"])

    def test_no_neighbor_is_invented_across_different_documents(self):
        entries = [item(0, "The projection is", continues_on=2), item(1, "\\hat y = 4u.", continues_from=1)]
        entries[1]["sources"][0]["document_key"] = "file:other"
        self.assertEqual(len(select_overview(entries, max_passages=1)), 1)

    def test_split_chunks_on_the_same_page_keep_their_neighbor(self):
        entries = [item(0, "Beginning of a long explanation", start_character=0, end_character=1900),
                   item(1, "continued explanation.", start_character=1700, end_character=2500)]
        entries[1]["sources"][0]["locations"][0]["page_number"] = 1
        self.assertEqual(len(select_overview(entries, max_passages=1)), 0)
        self.assertEqual(len(select_overview(entries, max_passages=2)), 2)

    def test_overview_intent_does_not_consume_a_focused_question(self):
        docs = [document([page(1, "Exercise material.")])]
        resolution = {"document_keys": ["file:10"], "supporting_document_keys": []}
        for question in ("explain practice questions 10", "explain this pdf", "summarize this document",
                         "can you explain me practice questions which are in practice questions 10 pdf"):
            self.assertTrue(overview_requested(question, resolution, docs), question)
        for question in ("explain question 7", "what does part b mean?", "explain orthogonality in Practice Questions 10"):
            self.assertFalse(overview_requested(question, resolution, docs), question)


class SavedOverviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.store = IngestionStore("student", Path(self.temp.name) / "saved.sqlite3")
        self.store.save_course({"course_id": 42, "synced_at": "now"})
        self.provider = FakeProvider()

    def save(self, doc):
        self.store.save_document(42, doc, "now")
        ensure_course_chunks(42, self.store)
        embed_course(42, store=self.store, provider=self.provider)
        self.provider.calls.clear()

    def retrieve(self, question, **kwargs):
        return retrieval_service.retrieve_evidence(42, question, store=self.store, provider=self.provider, **kwargs)

    def test_focused_section_reserves_next_pages_before_summary_matches(self):
        self.save(document([
            page(1, 'Question 2. Ordering language\n1. Every order begins with a keyword.'),
            page(2, '2. Include an item.\n3. Finish the order.\nb. Rewrite the grammar in EBNF.'),
            page(3, 'Use braces for repetition and brackets for optional components.'),
            page(4, 'Question 3. A different exercise begins.'),
            page(5, 'Question 2. Ordering language\nQuestion 3. Other exercise\nQuestion 4. Another exercise'),
        ]))
        report = self.retrieve('Explain question 2 part b in Practice Questions 10', document_keys=['file:10'], retrieval_mode='focused')
        pages = [item['sources'][0]['locations'][0]['page_number'] for item in report['results']]
        self.assertEqual(pages, [1, 2, 3])
        self.assertIn('Rewrite the grammar in EBNF', report['results'][1]['text'])
        self.assertLessEqual(len(report['results']), 8)

    def test_overview_reads_ordered_index_without_embedding_a_question(self):
        self.save(boundary_document())
        report = self.retrieve("explain practice questions 10")
        self.assertEqual(report["retrieval_mode"], "overview")
        self.assertEqual(report["evidence_coverage"], {"selected": 2, "available": 2})
        self.assertEqual([r["sources"][0]["locations"][0]["page_number"] for r in report["results"]], [1, 2])
        self.assertFalse(self.provider.calls)

    def test_focused_question_includes_next_page_by_document_order(self):
        self.save(boundary_document())
        report = self.retrieve("explain question 7", focus_documents=["file:10"])
        self.assertEqual(report["retrieval_mode"], "focused")
        self.assertTrue(self.provider.calls)
        self.assertEqual({r["sources"][0]["locations"][0]["page_number"] for r in report["results"]}, {1, 2})
        self.assertTrue(any(r.get("selection") == "continuation" for r in report["results"]))

    def test_focused_continuation_includes_previous_page(self):
        self.save(boundary_document())
        available = self.store.load_embedding_inputs(42)
        second = next(entry for entry in available if entry["sources"][0]["locations"][0]["page_number"] == 2)
        with patch.object(retrieval_service, "search_course", return_value={"status": "ready", "results": [{**second, "score": .9}]}):
            report = self.retrieve("explain the unit vector formula", focus_documents=["file:10"])
        self.assertEqual({r["sources"][0]["locations"][0]["page_number"] for r in report["results"]}, {1, 2})
        self.assertEqual([r["sources"][0]["locations"][0]["page_number"] for r in report["results"]], [1, 2])

    def test_true_unreadable_content_keeps_its_warning(self):
        doc = boundary_document();doc["pages"][0]["unreadable"].append("An image is unreadable.")
        self.save(doc)
        llm = FakeLLM()
        report = rag_service.answer_question(42, "explain practice questions 10", store=self.store,
                    embedding_provider=self.provider, llm_provider=llm)
        self.assertIn(UNREADABLE_MESSAGE, report["warnings"])
        self.assertIn(UNREADABLE_MESSAGE, llm.calls[0][3][0]["limitations"])

    def test_normal_continuation_has_no_incomplete_warning_in_llm_context(self):
        self.save(boundary_document());llm = FakeLLM()
        report = rag_service.answer_question(42, "explain practice questions 10", store=self.store,
                    embedding_provider=self.provider, llm_provider=llm)
        self.assertEqual(report["warnings"], [])
        self.assertTrue(all(not p["limitations"] for p in llm.calls[0][3]))
        self.assertNotIn("not visible", json.dumps(llm.calls[0][3]))

    def test_limited_overview_is_not_labelled_extraction_failure(self):
        self.save(document([page(n + 1, f"Section {n}. " + "Readable content. " * 50) for n in range(50)]))
        llm = FakeLLM()
        report = rag_service.answer_question(42, "summarize Practice Questions 10", store=self.store,
                    embedding_provider=self.provider, llm_provider=llm)
        self.assertIn(OVERVIEW_MESSAGE, report["warnings"])
        self.assertNotIn(UNREADABLE_MESSAGE, report["warnings"])
        self.assertLess(report["evidence_coverage"]["selected"], report["evidence_coverage"]["available"])

    def test_missing_index_does_not_claim_unreadable_content(self):
        self.store.save_document(42, boundary_document(), "now");ensure_course_chunks(42, self.store)
        llm = FakeLLM()
        report = rag_service.answer_question(42, "explain Practice Questions 10", store=self.store,
                    embedding_provider=self.provider, llm_provider=llm)
        self.assertEqual(report["status"], "indexing_needed")
        self.assertNotIn(UNREADABLE_MESSAGE, report["warnings"])
        self.assertFalse(llm.calls or self.provider.calls)

    def test_saved_reassessment_updates_metadata_and_reuses_existing_embeddings(self):
        raw = boundary_document();self.save(raw)
        # Emulate a pre-fix record without sending anything to Canvas/Vision.
        with self.store._connection() as connection:
            row = connection.execute("SELECT document_json, attempt_json FROM documents").fetchone()
            payload, attempt = json.loads(row[0]), json.loads(row[1])
            payload.update(pages=raw["pages"], status="partial");attempt["status"] = "partial"
            connection.execute("UPDATE documents SET document_json = ?, attempt_json = ?", (json.dumps(payload), json.dumps(attempt)))
        ensure_course_chunks(42, self.store)
        original_text = self.store.load_document(42, "file:10")["text"]
        result = reassess_saved_course(42, self.store)
        self.assertEqual(result["promoted"], 1)
        self.assertEqual(self.store.load_document(42, "file:10")["status"], "ok")
        self.assertEqual(self.store.load_document(42, "file:10")["text"], original_text)
        self.assertEqual(embed_course(42, store=self.store, provider=self.provider)["generated"], 0)
        self.assertFalse(self.provider.calls)
        repeat = reassess_saved_course(42, self.store)
        self.assertEqual(repeat["changed"], 0)
        self.assertEqual(repeat["chunking"]["reused"], 1)

    def test_reassessment_does_not_resurrect_failed_attempts(self):
        self.save(boundary_document())
        self.store.save_document(42, {"document_key": "file:10", "version": "new", "status": "error", "text": ""}, "now")
        self.assertEqual(reassess_saved_course(42, self.store)["skipped"], 1)
        self.assertTrue(self.store.load_document(42, "file:10")["stale"])

    def test_reassessment_cannot_cross_account_or_course(self):
        self.save(boundary_document())
        other = IngestionStore("other", self.store.path)
        other.save_course({"course_id": 42, "synced_at": "now"})
        other.save_document(42, boundary_document(), "now")
        self.store.save_course({"course_id": 43, "synced_at": "now"})
        self.store.save_document(43, {**boundary_document(), "course_id": 43}, "now")
        # All accounts/courses contain a legacy boundary-only warning needing
        # correction. Only the selected namespace/course may be updated.
        with self.store._connection() as connection:
            rows = connection.execute("SELECT namespace, course_id, document_json, attempt_json FROM documents").fetchall()
            for row in rows:
                payload, attempt = json.loads(row[2]), json.loads(row[3])
                payload.update(pages=boundary_document()["pages"], status="partial");attempt["status"] = "partial"
                connection.execute("UPDATE documents SET document_json = ?, attempt_json = ? WHERE namespace = ? AND course_id = ?",
                                   (json.dumps(payload), json.dumps(attempt), row[0], row[1]))
        original_other = other.load_document(42, "file:10")
        original_course = self.store.load_document(43, "file:10")
        self.assertEqual(reassess_saved_course(42, self.store)["promoted"], 1)
        self.assertEqual(other.load_document(42, "file:10"), original_other)
        self.assertEqual(self.store.load_document(43, "file:10"), original_course)


if __name__ == "__main__":
    unittest.main()
