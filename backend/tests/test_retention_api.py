"""HTTP cleanup validation and safe errors; no real database or Canvas access."""
import asyncio
import json
import unittest
from unittest.mock import Mock, patch

import requests

from backend.app import main, retention_service


async def request(method, path, payload=None):
    messages = []
    async def receive():
        return {'type': 'http.request', 'body': json.dumps(payload).encode(), 'more_body': False}
    async def send(message):
        messages.append(message)
    await main.app({'type': 'http', 'asgi': {'version': '3.0'}, 'http_version': '1.1', 'method': method,
                    'scheme': 'http', 'path': path, 'raw_path': path.encode(), 'query_string': b'',
                    'root_path': '', 'headers': [(b'content-type', b'application/json')],
                    'server': ('test', 80), 'client': ('test', 1)}, receive, send)
    return (next(m['status'] for m in messages if m['type'] == 'http.response.start'),
            json.loads(b''.join(m.get('body', b'') for m in messages if m['type'] == 'http.response.body')))


class RetentionApiTests(unittest.TestCase):
    def test_preview_and_explicit_cleanup_pass_only_selected_ids_and_preview(self):
        with patch.object(retention_service, 'preview_cleanup', return_value={'courses': []}) as preview:
            self.assertEqual(asyncio.run(request('GET', '/api/storage'))[0], 200)
            preview.assert_called_once_with()
        with patch.object(retention_service, 'delete_materials', return_value={'deleted_course_ids': [10]}) as delete:
            code, _ = asyncio.run(request('POST', '/api/storage/cleanup', {'course_ids': [10], 'preview_id': 'a' * 64}))
            self.assertEqual(code, 200)
            delete.assert_called_once_with([10], 'a' * 64)

    def test_bad_inputs_never_reach_deletion(self):
        with patch.object(retention_service, 'delete_materials') as delete:
            for ids, token in (([], 'a' * 64), ([True], 'a' * 64), (['10'], 'a' * 64), ([10], 'bad')):
                self.assertEqual(asyncio.run(request('POST', '/api/storage/cleanup',
                                 {'course_ids': ids, 'preview_id': token}))[0], 422)
            delete.assert_not_called()

    def test_stale_preview_is_conflict_and_provider_errors_are_safe(self):
        with patch.object(retention_service, 'delete_materials', side_effect=retention_service.RetentionConflict('Refresh the preview.')):
            self.assertEqual(asyncio.run(request('POST', '/api/storage/cleanup', {'course_ids': [10], 'preview_id': 'a' * 64}))[0], 409)
        with patch.object(retention_service, 'preview_cleanup', side_effect=requests.ConnectionError('secret provider detail')):
            code, body = asyncio.run(request('GET', '/api/storage'))
            self.assertEqual(code, 503)
            self.assertNotIn('secret', json.dumps(body))

    def test_keep_choice_uses_current_account_store(self):
        store = Mock()
        with patch.object(main, 'default_store', return_value=store):
            self.assertEqual(asyncio.run(request('PATCH', '/api/storage/courses/10', {'keep': True}))[0], 200)
        store.set_course_kept.assert_called_once_with(10, True)
