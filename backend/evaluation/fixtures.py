"""Synthetic course fixtures and explicit provider replays, not language models."""

import copy
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from backend.app.chunking_service import ensure_course_chunks
from backend.app.embedding_service import embed_course
from backend.app.ingestion_store import IngestionStore
from backend.app.query_understanding_service import empty_understanding
from backend.tests.test_embeddings import FakeProvider

NOW = datetime(2026, 11, 1, 18, tzinfo=timezone.utc)
POLICY = ("Generative AI tools are not permitted for this assignment. You may not use them "
          "to generate, rewrite, or substantially revise answers. Your responses must reflect your own understanding.")


class EvaluationFixture:
    """Build an isolated SQLite course with deliberately distinct numbered sources."""

    def __init__(self):
        self.directory = tempfile.TemporaryDirectory()
        self.store = IngestionStore('evaluation-synthetic', Path(self.directory.name) / 'evaluation.sqlite3')
        self.embedding = FakeProvider()
        self.assignments = []
        for aid, title, text, due in (
            (101, 'HW 1', 'Chapter 1, Problem 5: analyze findLast and countPositive, parts (a) through (e). Part (f) is not required.', '2026-11-07T05:59:00Z'),
            (110, 'HW 10', 'Homework ten asks for a separate testing report.', '2026-11-10T05:59:00Z'),
            (106, 'HW 6', 'Explain the Chapter 6 problems. Read the linked questions and AI-use policy.', '2026-11-03T05:59:00Z'),
            (201, 'Iteration 3 Presentation', 'Present your design and working demonstration.', None),
        ):
            raw = {'id': aid, 'course_id': 42, 'name': title, 'description': text, 'due_at': due,
                   'points_possible': 100, 'submission': {'workflow_state': 'unsubmitted'},
                   'html_url': f'https://canvas.example/courses/42/assignments/{aid}'}
            if aid == 201:
                raw['rubric'] = [{'id': f'criterion-{i}', 'description': name, 'points': points,
                    'long_description': f'Explain the actual {name.lower()} expectations.', 'ratings': [
                        {'id': f'full-{i}', 'description': 'Complete', 'long_description': 'All requirements met.', 'points': points},
                        {'id': f'zero-{i}', 'description': 'Missing', 'points': 0}]}
                    for i, (name, points) in enumerate((('Content', 24), ('Slides', 24), ('Presentation style', 24), ('Demo', 28)))]
            record = {'canvas_id': aid, 'course_id': 42, 'source_type': 'assignment', 'title': title,
                      'url': raw['html_url'], 'metadata': raw}
            self.assignments.append(record)
        self.snapshot = {'course_id': 42, 'synced_at': '2026-11-01T12:00:00Z',
                         'course': {'metadata': {'course_code': 'CSE TEST', 'name': 'Synthetic evaluation course', 'time_zone': 'America/Chicago'}},
                         'assignments': self.assignments}
        self.store.save_course(self.snapshot)
        for a in self.assignments:
            self.save(f"assignment:{a['canvas_id']}", a['title'], a['metadata']['description'], source_type='assignment')
        for fid, title, aid, text in (
            (501, 'HW1_questions.pdf', 101, 'findLast searches an array for the last occurrence of a value. countPositive counts positive elements. Analyze the fault and test behavior in parts (a)-(e).'),
            (502, 'HW1_questions_solutions.pdf', 101, 'Published solutions: completed repairs and test cases.'),
            (510, 'HW10_questions.pdf', 110, 'Homework ten: analyze the testing report, not homework one.'),
            (506, 'HW6_questions.pdf', 106, 'Chapter 6 homework instructions. ' + POLICY),
            (507, 'HW6_solutions.pdf', 106, 'An intentionally tempting solution file; the assignment policy still applies.'),
            (1, 'Practice Questions 1.pdf', None, 'Question 1: matrix multiplication, a different practice set.'),
        ):
            self.save(f'file:{fid}', title, text, assignment_id=aid)
        pages = [{'page_number': i, 'text': text, 'status': 'ok'} for i, text in enumerate((
            'Section 1. Question 1: Verify the vectors are orthogonal.',
            'Section 2. Question 7: The projection is',
            'given by the inner product formula. Continue question 7 with the error vector.',
            'Section 3. Least squares and approximation exercises.'), 1)]
        self.save('file:10', 'Practice Questions 10.pdf', '', pages=pages)
        self.save('file:700', 'Attendance policy.txt', 'Attendance is required for the laboratory sessions.')
        ensure_course_chunks(42, self.store)
        embed_course(42, store=self.store, provider=self.embedding)
        self.embedding.calls.clear()

    def save(self, key, title, text, *, source_type='file', assignment_id=None, **extra):
        self.store.save_document(42, {'document_key': key, 'course_id': 42, 'canvas_id': int(key.split(':')[1]),
            'source_type': source_type, 'title': title, 'status': 'ok', 'text': text,
            'url': f'https://canvas.example/courses/42/{key.replace(":", "/")}',
            'references': [{'source_type': 'assignment', 'canvas_id': assignment_id}] if assignment_id else [], **extra}, self.snapshot['synced_at'])

    def context(self, name):
        ids, docs = {'hw1': ([101], ['assignment:101', 'file:501']), 'hw6': ([106], ['assignment:106', 'file:506']),
                     'rubric': ([201], ['assignment:201']), 'practice10': ([], ['file:10']), 'none': ([], [])}[name]
        known = {d['document_key']: d for d in self.store.load_documents(42)}
        return {'active_assignments': [{'assignment_id': a['canvas_id'], 'title': a['title']} for a in self.assignments if a['canvas_id'] in ids],
                'active_documents': [{'document_key': key, 'title': known[key]['title']} for key in docs],
                'recent_turns': [{'question': 'Explain ' + known[docs[0]]['title'], 'answer': 'A previous supported explanation.'}] if docs else []}

    def plan(self, case):
        return empty_understanding(case['question'], **copy.deepcopy(case['fixture_plan']))

    def close(self):
        self.directory.cleanup()


class ReplayProvider:
    """Supply declared plans/answers to exercise plumbing; never claim NLP accuracy."""

    def __init__(self, plan, *, unsafe=False):
        self.plan, self.unsafe = plan, unsafe
        self.contexts, self.rewrite_calls = [], 0

    async def rewrite_query(self, course_id, question, context):
        self.rewrite_calls += 1
        return copy.deepcopy(self.plan)

    def generate(self, instructions, course_id, question, passages):
        self.contexts.append(copy.deepcopy(passages))
        text = ('Here is the complete submission-ready solution: def solve(): return 42' if self.unsafe
                else 'A supported explanation. ' + passages[0]['text'][:180])
        return {'answer': {'status': 'answered', 'blocks': [{'text': text, 'source_ids': [passages[0]['id']]}],
                           'limitations': []}, 'model': 'declared-replay-not-a-model', 'usage': None}

    async def stream_generate(self, instructions, course_id, question, passages, context):
        response = self.generate(instructions, course_id, question, passages)
        yield {'event': 'answer_delta', 'data': {'blocks': [response['answer']['blocks'][0]['text']]}}
        yield {'event': 'complete', 'data': response}
