"""Semester cleanup uses temporary SQLite data and fake Canvas responses only."""

import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import requests

from backend.app import retention_service as retention
from backend.app.chunking_service import ensure_course_chunks
from backend.app.conversation_store import ConversationStore
from backend.app.embedding_service import embed_course
from backend.app.ingestion_store import IngestionStore, MATERIAL_TABLES
from backend.tests.test_embeddings import FakeProvider

NOW = datetime(2030, 6, 15, tzinfo=timezone.utc)
CURRENT_TERM = {'id': 20, 'name': 'Summer', 'start_at': '2030-06-01T00:00:00Z', 'end_at': '2030-08-01T00:00:00Z'}


class RetentionTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.store = IngestionStore('student', Path(directory.name) / 'data.sqlite3')
        self.enterContext(patch.dict(os.environ, {'CANVAS_TERM_ID': ''}))
        self.current = self.enterContext(patch.object(retention.canvas_service, 'get_current_courses',
                                                     return_value=[{'id': 90, 'term': CURRENT_TERM}]))
        self.seed(90, CURRENT_TERM)
        self.seed(10, {'id': 10, 'name': 'Spring', 'start_at': '2030-01-01T00:00:00Z', 'end_at': '2030-05-01T00:00:00Z'})
        self.store.save_sync_state({'status': 'completed', 'failed_courses': 0,
                                   'courses': [{'course_id': 90, 'status': 'completed'}]})

    def seed(self, course_id, term, store=None):
        store = store or self.store
        store.save_course({'course_id': course_id, 'synced_at': 'now',
                           'course': {'metadata': {'id': course_id, 'name': 'Test course', 'course_code': f'TEST {course_id}', 'term': term}}})
        store.save_document(course_id, {'course_id': course_id, 'document_key': 'file:1', 'title': 'Notes',
                                       'source_type': 'file', 'status': 'ok', 'text': 'Meaningful course material for this test.'}, 'now')
        ensure_course_chunks(course_id, store)
        embed_course(course_id, store=store, provider=FakeProvider())
        with store._connection() as connection:
            connection.execute('INSERT INTO visual_extractions VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
                               (store.namespace, course_id, 'hash', 'page', 1, 'config', '{}', 'now'))

    def preview(self):
        return retention.preview_cleanup(store=self.store, now=NOW)

    def delete(self, ids, preview=None):
        return retention.delete_materials(ids, (preview or self.preview())['preview_id'], store=self.store, now=NOW)

    def test_preview_is_read_only_and_reports_real_payload_counts(self):
        before = self.store.load_document(10, 'file:1')
        result = self.preview()
        courses = {c['course_id']: c for c in result['courses']}
        self.assertTrue(courses[10]['eligible'])
        self.assertFalse(courses[90]['eligible'])
        self.assertGreater(result['eligible_bytes'], 0)
        self.assertEqual(courses[10]['counts']['chunk_embeddings'], 1)
        self.assertEqual(before, self.store.load_document(10, 'file:1'))

    def test_expired_materials_are_deleted_in_all_tables_but_chats_and_other_accounts_survive(self):
        other = IngestionStore('another-student', self.store.path)
        self.seed(10, CURRENT_TERM, other)
        chats = ConversationStore(self.store.path)
        chats.create(self.store.namespace, 10, 'old-chat')
        result = self.delete([10])
        self.assertEqual(result['deleted_course_ids'], [10])
        with self.store._connection() as connection:
            for table in MATERIAL_TABLES:
                self.assertEqual(connection.execute(f'SELECT count(*) FROM {table} WHERE namespace=? AND course_id=10',
                                                    (self.store.namespace,)).fetchone()[0], 0)
        self.assertIsNotNone(chats.load(self.store.namespace, 10, 'old-chat'))
        self.assertIsNotNone(other.load_document(10, 'file:1'))
        self.assertIsNotNone(self.store.load_document(90, 'file:1'))
        self.assertTrue(self.store.load_course(10)['materials_removed'])

    def test_grace_period_and_unknown_or_invalid_dates_are_protected(self):
        for term in (None, {}, {'start_at': 'bad', 'end_at': 'bad'},
                     {'start_at': '2030-01-01T00:00:00Z', 'end_at': '2030-06-01T00:00:00Z'}):
            with self.subTest(term=term):
                snapshot = self.store.load_course(10)
                snapshot['course']['metadata']['term'] = term
                self.store.save_course(snapshot)
                self.assertFalse(next(c for c in self.preview()['courses'] if c['course_id'] == 10)['eligible'])

    def test_kept_choice_persists_and_invalidates_existing_preview(self):
        preview = self.preview()
        self.store.set_course_kept(10, True)
        with self.assertRaises(retention.RetentionConflict):
            self.delete([10], preview)
        self.assertFalse(next(c for c in self.preview()['courses'] if c['course_id'] == 10)['eligible'])
        self.store.set_course_kept(10, False)
        self.assertTrue(next(c for c in self.preview()['courses'] if c['course_id'] == 10)['eligible'])

    def test_current_course_or_unselected_account_cannot_be_deleted(self):
        for ids in ([90], [999], [10, 90]):
            with self.subTest(ids=ids), self.assertRaises(retention.RetentionConflict):
                self.delete(ids)
        self.assertIsNotNone(self.store.load_document(10, 'file:1'))

    def test_empty_failed_and_unconfirmed_current_courses_never_enable_cleanup(self):
        for current in ([], [{'id': 90, 'term': {}}]):
            self.current.return_value = current
            preview = self.preview()
            self.assertTrue(preview['blocked_reason'])
            with self.assertRaises(retention.RetentionConflict):
                self.delete([10], preview)
        self.current.side_effect = requests.ConnectionError('private')
        with self.assertRaises(requests.RequestException):
            self.delete([10], {'preview_id': '0' * 64})
        self.assertIsNotNone(self.store.load_document(10, 'file:1'))

    def test_queued_running_failed_or_incomplete_sync_protects_data(self):
        for state in ({'status': 'running'}, {'status': 'queued'}, {'status': 'error'},
                      {'status': 'completed', 'courses': [], 'failed_courses': 0}):
            self.store.save_sync_state(state)
            self.assertTrue(self.preview()['blocked_reason'])
            with self.assertRaises(retention.RetentionConflict):
                self.delete([10])

    def test_term_override_and_stale_preview_cannot_delete_data(self):
        with patch.dict(os.environ, {'CANVAS_TERM_ID': '10'}):
            self.assertTrue(self.preview()['blocked_reason'])
        preview = self.preview()
        snapshot = self.store.load_course(10)
        snapshot['synced_at'] = 'changed'
        self.store.save_course(snapshot)
        with self.assertRaises(retention.RetentionConflict):
            self.delete([10], preview)

    def test_mid_cleanup_failure_rolls_back_material_removal(self):
        remove = self.store.remove_course_materials
        def fail(connection, ids):
            remove(connection, ids)
            raise sqlite3.OperationalError('simulated disk failure')
        with patch.object(self.store, 'remove_course_materials', side_effect=fail):
            with self.assertRaises(sqlite3.OperationalError):
                self.delete([10])
        self.assertIsNotNone(self.store.load_document(10, 'file:1'))
        self.assertFalse(self.store.load_course(10).get('materials_removed'))

    def test_busy_compaction_reports_successful_deletion_without_claiming_disk_shrinkage(self):
        with patch.object(self.store, 'compact', side_effect=sqlite3.OperationalError('busy')):
            result = self.delete([10])
        self.assertFalse(result['compacted'])
        self.assertIsNone(self.store.load_document(10, 'file:1'))
