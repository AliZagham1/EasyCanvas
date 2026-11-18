"""SQLite conversation/message persistence, separate from course ingestion."""

from contextlib import contextmanager, closing
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import uuid
import unicodedata

from backend.app.ingestion_store import DEFAULT_DATABASE


def validate_title(title):
    """Names are single-line Unicode text, independent of chat content/state."""
    if not isinstance(title, str):
        raise ValueError('Conversation name must be text.')
    title = title.strip()
    if not title or len(title) > 100 or any(unicodedata.category(c)=='Cc' for c in title):
        raise ValueError('Use a conversation name between 1 and 100 characters without control characters.')
    return title


class ConversationStore:
    """Scope every operation by account, course and conversation; migrate lazily."""

    def __init__(self, path=DEFAULT_DATABASE):
        self.path = Path(path)

    @contextmanager
    def connection(self):
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with closing(sqlite3.connect(self.path, timeout=30)) as connection, connection:
            connection.row_factory = sqlite3.Row
            connection.execute('PRAGMA foreign_keys=ON')
            connection.executescript('''
                CREATE TABLE IF NOT EXISTS assistant_conversations (
                    namespace TEXT NOT NULL, conversation_id TEXT NOT NULL,
                    course_id INTEGER NOT NULL, title TEXT NOT NULL,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    state_json TEXT NOT NULL,
                    title_is_custom INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY(namespace, conversation_id)
                );
                CREATE TABLE IF NOT EXISTS assistant_messages (
                    namespace TEXT NOT NULL, conversation_id TEXT NOT NULL,
                    message_id TEXT NOT NULL, turn_id TEXT NOT NULL,
                    role TEXT NOT NULL CHECK(role IN ('user','assistant')),
                    content TEXT NOT NULL, created_at TEXT NOT NULL, response_json TEXT,
                    PRIMARY KEY(namespace, message_id),
                    UNIQUE(namespace, conversation_id, turn_id, role),
                    FOREIGN KEY(namespace, conversation_id)
                        REFERENCES assistant_conversations(namespace, conversation_id)
                );
                CREATE INDEX IF NOT EXISTS assistant_course_history
                    ON assistant_conversations(namespace, course_id, updated_at DESC);
            ''')
            # Additive migration for databases created before custom titles.
            connection.execute('BEGIN IMMEDIATE')
            columns = {row['name'] for row in connection.execute('PRAGMA table_info(assistant_conversations)')}
            if 'title_is_custom' not in columns:
                connection.execute('ALTER TABLE assistant_conversations ADD COLUMN title_is_custom INTEGER NOT NULL DEFAULT 0')
            connection.commit()
            yield connection

    def create(self, namespace, course_id, conversation_id):
        now = datetime.now(timezone.utc).isoformat()
        with self.connection() as c:
            c.execute('INSERT INTO assistant_conversations (namespace,conversation_id,course_id,title,created_at,updated_at,state_json) VALUES (?,?,?,?,?,?,?)',
                      (namespace, conversation_id, course_id, 'New conversation', now, now, '{}'))

    def list(self, namespace, course_id):
        with self.connection() as c:
            return [dict(r) for r in c.execute('''SELECT conversation_id, course_id, title, created_at, updated_at
                FROM assistant_conversations WHERE namespace=? AND course_id=? ORDER BY updated_at DESC, rowid DESC''', (namespace, course_id))]

    def load(self, namespace, course_id, conversation_id, *, touch=False):
        with self.connection() as c:
            row = c.execute('SELECT * FROM assistant_conversations WHERE namespace=? AND course_id=? AND conversation_id=?',
                            (namespace, course_id, conversation_id)).fetchone()
            if row is None:
                return None
            result = dict(row)
            result.pop('namespace')
            result['state'] = json.loads(result.pop('state_json'))
            result['messages'] = [{**dict(m), 'response': json.loads(m['response_json']) if m['response_json'] else None}
                for m in c.execute('''SELECT message_id, turn_id, role, content, created_at, response_json FROM assistant_messages
                    WHERE namespace=? AND conversation_id=? ORDER BY rowid''', (namespace, conversation_id))]
            for message in result['messages']:
                message.pop('response_json')
            if touch:
                c.execute('UPDATE assistant_conversations SET updated_at=? WHERE namespace=? AND conversation_id=?',
                          (datetime.now(timezone.utc).isoformat(), namespace, conversation_id))
            return result

    def save_turn(self, session, turn_id, question, response):
        """Atomically save the validated exchange and its compact resolved state."""
        now = datetime.now(timezone.utc).isoformat()
        with self.connection() as c:
            row = c.execute('SELECT title FROM assistant_conversations WHERE namespace=? AND course_id=? AND conversation_id=?',
                            (session.namespace, session.course_id, session.id)).fetchone()
            if row is None:
                raise ValueError('Conversation is unavailable.')
            for role, content, payload in [('user', question, None), ('assistant', response['answer'], json.dumps(response))]:
                c.execute('INSERT OR IGNORE INTO assistant_messages VALUES (?,?,?,?,?,?,?,?)',
                          (session.namespace, session.id, str(uuid.uuid4()), turn_id, role, content, now, payload))
            c.execute("UPDATE assistant_conversations SET title=CASE WHEN title_is_custom=0 AND title='New conversation' THEN ? ELSE title END, updated_at=?, state_json=? WHERE namespace=? AND conversation_id=?",
                      (question[:100], now, json.dumps({'semantic': session.state, 'active_documents': session.active_documents,
                                             'active_assignments': session.active_assignments, 'clarification': session.clarification}), session.namespace, session.id))

    def begin_turn(self, session, turn_id, question):
        """Keep interrupted questions recoverable without saving unvalidated drafts."""
        with self.connection() as c:
            old = c.execute("SELECT content FROM assistant_messages WHERE namespace=? AND conversation_id=? AND turn_id=? AND role='user'",
                            (session.namespace, session.id, turn_id)).fetchone()
            if old and old['content'] != question:
                raise ValueError('Turn ID was already used for another question.')
            c.execute('INSERT OR IGNORE INTO assistant_messages VALUES (?,?,?,?,?,?,?,?)',
                      (session.namespace, session.id, str(uuid.uuid4()), turn_id, 'user', question, datetime.now(timezone.utc).isoformat(), None))
            c.execute("UPDATE assistant_conversations SET title=CASE WHEN title_is_custom=0 AND title='New conversation' THEN ? ELSE title END, updated_at=? WHERE namespace=? AND conversation_id=?",
                      (question[:100], datetime.now(timezone.utc).isoformat(), session.namespace, session.id))

    def rename(self, namespace, course_id, conversation_id, title):
        """Change only display-title metadata; message IDs and state stay intact."""
        title = validate_title(title)
        with self.connection() as c:
            changed = c.execute('UPDATE assistant_conversations SET title=?, title_is_custom=1, updated_at=? WHERE namespace=? AND course_id=? AND conversation_id=?',
                                (title,datetime.now(timezone.utc).isoformat(),namespace,course_id,conversation_id)).rowcount
            if not changed:
                return None
            return dict(c.execute('SELECT conversation_id,course_id,title,created_at,updated_at FROM assistant_conversations WHERE namespace=? AND conversation_id=?',
                                  (namespace,conversation_id)).fetchone())

    def delete(self, namespace, course_id, conversation_id):
        """Delete a single thread and its messages atomically, never course data."""
        with self.connection() as c:
            c.execute('BEGIN IMMEDIATE')
            if c.execute('SELECT 1 FROM assistant_conversations WHERE namespace=? AND course_id=? AND conversation_id=?',
                         (namespace,course_id,conversation_id)).fetchone() is None:
                return False
            c.execute('DELETE FROM assistant_messages WHERE namespace=? AND conversation_id=?',(namespace,conversation_id))
            c.execute('DELETE FROM assistant_conversations WHERE namespace=? AND course_id=? AND conversation_id=?',(namespace,course_id,conversation_id))
            return True
