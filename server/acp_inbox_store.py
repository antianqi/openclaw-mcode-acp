"""
ACP Inbox Store — Peer-to-peer messaging between goudan and mavis (v7-bidir)
============================================================================
Persistent message bus for agent-to-agent communication.
- Both goudan and mavis can write/read
- Supports question/answer with blocking wait (long-poll)
- Thread-safe SQLite (WAL mode)

Schema:
  acp_inbox (
    id          INTEGER PRIMARY KEY,
    session_id  TEXT NOT NULL,    -- grouping (e.g. 'xls-2026-07')
    sender      TEXT NOT NULL,    -- 'goudan' | 'mavis' | 'boss' | 'system'
    msg_type    TEXT NOT NULL,    -- 'message' | 'question' | 'answer'
    content     TEXT NOT NULL,    -- JSON or plain text
    parent_id   INTEGER,          -- answer → question linkage
    created_at  INTEGER NOT NULL, -- ms timestamp
    read_at     INTEGER,          -- when receiver fetched
    answered_at INTEGER           -- when question got answered
  )

Usage:
    from acp_inbox_store import InboxStore

    store = InboxStore()  # auto-creates table

    # goudan writes to mavis
    msg_id = store.write('session-1', 'goudan', 'hello mavis')

    # mavis reads new messages
    pending = store.read_pending('session-1', since_id=0)

    # mavis asks goudan a blocking question
    qid = store.ask_question('session-1', 'mavis', 'store_code 怎么推断?')
    answer = store.wait_for_answer(qid, timeout=300)

    # goudan answers
    store.answer_question(qid, '查 platform_name_mapping 集合')
"""
import os
import json
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Optional, List, Dict, Any

def _acp_default_db_path() -> str:
    """Cross-platform default DB path: $ACP_TEMP_DIR or stdlib tempdir."""
    override = os.environ.get('ACP_TEMP_DIR')
    if override:
        return str(Path(override).expanduser().resolve() / 'acp-tasks.db')
    import tempfile
    return str(Path(tempfile.gettempdir()).resolve() / 'acp-tasks.db')


DEFAULT_DB_PATH = os.environ.get('ACP_DB_PATH') or _acp_default_db_path()


class InboxStore:
    """Thread-safe SQLite-backed inbox for peer-to-peer agent messaging."""

    def __init__(self, db_path: str = DEFAULT_DB_PATH):
        self.db_path = db_path
        self._lock = threading.Lock()
        self._init_db()

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(self.db_path, timeout=10, isolation_level=None)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            yield conn
        finally:
            conn.close()

    def _init_db(self):
        with self._conn() as conn:
            conn.executescript('''
                CREATE TABLE IF NOT EXISTS acp_inbox (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    sender TEXT NOT NULL,
                    msg_type TEXT NOT NULL DEFAULT 'message',
                    content TEXT NOT NULL,
                    parent_id INTEGER,
                    created_at INTEGER NOT NULL,
                    read_at INTEGER,
                    answered_at INTEGER
                );
                CREATE INDEX IF NOT EXISTS idx_inbox_session
                    ON acp_inbox(session_id, created_at);
                CREATE INDEX IF NOT EXISTS idx_inbox_session_id
                    ON acp_inbox(session_id, id);
                CREATE INDEX IF NOT EXISTS idx_inbox_parent
                    ON acp_inbox(parent_id) WHERE parent_id IS NOT NULL;
                CREATE INDEX IF NOT EXISTS idx_inbox_type
                    ON acp_inbox(session_id, msg_type);
            ''')

    # ---------- writes ----------

    def write(self, session_id: str, sender: str, content: Any,
              msg_type: str = 'message', parent_id: Optional[int] = None) -> int:
        """Write a message to inbox. Returns message id."""
        if isinstance(content, (dict, list)):
            content = json.dumps(content, ensure_ascii=False)
        with self._lock, self._conn() as conn:
            cur = conn.execute('''
                INSERT INTO acp_inbox (session_id, sender, msg_type, content, parent_id, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (
                session_id,
                sender,
                msg_type,
                content,
                parent_id,
                int(time.time() * 1000),
            ))
            return cur.lastrowid

    def ask_question(self, session_id: str, sender: str, question: Any) -> int:
        """Write a question (blocking wait pattern). Returns question id."""
        return self.write(session_id, sender, question, msg_type='question')

    def answer_question(self, question_id: int, answer: Any) -> int:
        """Answer a question. Returns answer id. Sets answered_at on parent."""
        # Look up parent to get session_id + sender
        with self._conn() as conn:
            row = conn.execute(
                'SELECT session_id, sender FROM acp_inbox WHERE id = ?',
                (question_id,)
            ).fetchone()
            if not row:
                raise ValueError(f'question_id {question_id} not found')

        answer_id = self.write(
            row['session_id'],
            # Answer sender is opposite of question sender
            'goudan' if row['sender'] != 'goudan' else 'mavis',
            answer,
            msg_type='answer',
            parent_id=question_id,
        )
        # Mark parent answered
        with self._lock, self._conn() as conn:
            conn.execute(
                'UPDATE acp_inbox SET answered_at = ? WHERE id = ? AND answered_at IS NULL',
                (int(time.time() * 1000), question_id)
            )
        return answer_id

    def mark_read(self, message_ids: List[int]):
        """Mark messages as read."""
        if not message_ids:
            return
        with self._lock, self._conn() as conn:
            placeholders = ','.join('?' * len(message_ids))
            conn.execute(
                f'UPDATE acp_inbox SET read_at = ? WHERE id IN ({placeholders}) AND read_at IS NULL',
                [int(time.time() * 1000)] + list(message_ids)
            )

    # ---------- reads ----------

    def read_pending(self, session_id: str, since_id: int = 0,
                     sender: Optional[str] = None,
                     msg_type: Optional[str] = None,
                     limit: int = 50) -> List[Dict[str, Any]]:
        """Read new messages in a session since given id (exclusive).

        Args:
            session_id: session/group key
            since_id: only return messages with id > this (0 = all)
            sender: filter by sender (e.g. 'mavis' to see only mavis's)
            msg_type: filter by 'message' / 'question' / 'answer'
            limit: max rows to return

        Returns:
            list of message dicts, ordered by id ASC
        """
        where = ['session_id = ?', 'id > ?']
        vals = [session_id, since_id]
        if sender:
            where.append('sender = ?')
            vals.append(sender)
        if msg_type:
            where.append('msg_type = ?')
            vals.append(msg_type)
        sql = f'SELECT * FROM acp_inbox WHERE {" AND ".join(where)} ORDER BY id ASC LIMIT ?'
        vals.append(limit)
        with self._conn() as conn:
            rows = conn.execute(sql, vals).fetchall()
            return [self._row_to_dict(r) for r in rows]

    def get(self, message_id: int) -> Optional[Dict[str, Any]]:
        """Fetch a single message by id."""
        with self._conn() as conn:
            row = conn.execute(
                'SELECT * FROM acp_inbox WHERE id = ?', (message_id,)
            ).fetchone()
            return self._row_to_dict(row) if row else None

    def list_sessions(self, limit: int = 20) -> List[Dict[str, Any]]:
        """List recent sessions with last activity time."""
        with self._conn() as conn:
            rows = conn.execute('''
                SELECT session_id,
                       COUNT(*) AS msg_count,
                       MAX(created_at) AS last_activity,
                       SUM(CASE WHEN msg_type='question' THEN 1 ELSE 0 END) AS questions,
                       SUM(CASE WHEN msg_type='answer' THEN 1 ELSE 0 END) AS answers
                FROM acp_inbox
                GROUP BY session_id
                ORDER BY last_activity DESC
                LIMIT ?
            ''', (limit,)).fetchall()
            return [dict(r) for r in rows]

    # ---------- blocking waits ----------

    def wait_for_answer(self, question_id: int,
                        timeout: float = 300.0,
                        poll_interval: float = 0.5) -> Optional[Dict[str, Any]]:
        """Block (poll DB) until question is answered. Returns answer or None on timeout.

        Args:
            question_id: the question id to wait for
            timeout: max seconds to wait (default 5 min)
            poll_interval: seconds between DB checks (default 0.5s)

        Returns:
            dict with answer content, or None on timeout
        """
        deadline = time.time() + timeout
        while time.time() < deadline:
            with self._conn() as conn:
                row = conn.execute('''
                    SELECT a.* FROM acp_inbox a
                    WHERE a.parent_id = ? AND a.msg_type = 'answer'
                    ORDER BY a.id ASC LIMIT 1
                ''', (question_id,)).fetchone()
                if row:
                    return self._row_to_dict(row)
            time.sleep(poll_interval)
        return None

    # ---------- helpers ----------

    @staticmethod
    def _row_to_dict(row) -> Dict[str, Any]:
        d = dict(row)
        # Try to parse JSON content; fall back to raw string
        content = d.get('content')
        if isinstance(content, str):
            try:
                d['content'] = json.loads(content)
            except (json.JSONDecodeError, TypeError):
                pass  # keep as plain string
        # Convert epoch ms → ISO for human readability
        for ts_field in ('created_at', 'read_at', 'answered_at'):
            ts = d.get(ts_field)
            if ts:
                d[ts_field + '_iso'] = time.strftime(
                    '%Y-%m-%dT%H:%M:%S', time.localtime(ts / 1000)
                )
        return d


if __name__ == '__main__':
    # Self-test
    import tempfile
    test_db = os.path.join(tempfile.gettempdir(), 'acp-inbox-test.db')
    if os.path.exists(test_db):
        os.remove(test_db)
    store = InboxStore(test_db)
    print(f'DB: {test_db}')

    # goudan writes
    m1 = store.write('test-session', 'goudan', 'hello from goudan')
    print(f'goudan wrote: id={m1}')

    # mavis reads pending
    pending = store.read_pending('test-session')
    print(f'mavis read: {len(pending)} pending')
    assert len(pending) == 1
    assert pending[0]['sender'] == 'goudan'

    # mavis asks a question
    qid = store.ask_question('test-session', 'mavis', 'how do I parse XLS?')
    print(f'mavis asked: qid={qid}')

    # goudan answers
    store.answer_question(qid, 'use xlrd==1.2.0 with encoding_override=gbk')
    print(f'goudan answered qid={qid}')

    # mavis waits for answer (already there)
    answer = store.wait_for_answer(qid, timeout=2)
    print(f'mavis got answer: {answer["content"] if answer else None}')
    assert answer is not None
    assert 'xlrd' in str(answer['content'])

    # List sessions
    sessions = store.list_sessions()
    print(f'sessions: {sessions}')
    assert len(sessions) == 1

    print('[OK] InboxStore self-test passed')