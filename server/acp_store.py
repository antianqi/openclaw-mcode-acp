"""
ACP Task Store — SQLite-backed persistence (v3)
================================================
Persistent task storage for the OpenClaw ACP server.
Server restart preserves task history.

Default DB path: %TEMP%\acp-tasks.db (override via ACP_DB_PATH env).

Usage:
    from acp_store import TaskStore

    store = TaskStore()  # auto-creates table on first run

    store.insert(task_dict)            # when creating
    store.update(task_id, status='running', started_at=...)  # status changes
    store.get(task_id)                 # fetch one
    store.list(status='succeeded')     # filter
    store.list(workspace='C:/path', limit=20)
    store.count()                      # total count
    store.count(status='running')      # filtered count
    store.delete_old(keep_count=1000)  # GC: keep most recent N

All public methods are thread-safe.
"""
import os
import json
import sqlite3
import threading
import time
from contextlib import contextmanager
from typing import Optional, List, Dict, Any

DEFAULT_DB_PATH = os.path.expandvars(os.environ.get(
    'ACP_DB_PATH',
    r'%USERPROFILE%\AppData\Local\Temp\acp-tasks.db'
))

# Fields allowed to be updated via store.update()
ALLOWED_UPDATE_FIELDS = frozenset({
    'status', 'started_at', 'finished_at', 'answer',
    'session_id', 'duration_ms', 'error', 'mavis_raw',
})


class TaskStore:
    """Thread-safe SQLite-backed task store."""

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
                CREATE TABLE IF NOT EXISTS tasks (
                    id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    prompt TEXT NOT NULL,
                    workspace TEXT NOT NULL,
                    files TEXT NOT NULL DEFAULT '[]',
                    timeout TEXT NOT NULL DEFAULT '5m',
                    created_at INTEGER NOT NULL,
                    started_at INTEGER,
                    finished_at INTEGER,
                    answer TEXT,
                    session_id TEXT,
                    duration_ms INTEGER,
                    error TEXT,
                    mavis_raw TEXT,
                    updated_at INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_status_created ON tasks(status, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_workspace ON tasks(workspace, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_created_at ON tasks(created_at DESC);
            ''')

    # ---------- writes ----------

    def insert(self, task: Dict[str, Any]):
        """Insert a new task (status must be 'created')."""
        with self._lock, self._conn() as conn:
            conn.execute('''
                INSERT INTO tasks (id, status, prompt, workspace, files, timeout, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                task['id'],
                task['status'],
                task['prompt'],
                task['workspace'],
                json.dumps(task.get('files') or [], ensure_ascii=False),
                task.get('timeout', '5m'),
                task['created_at'],
                int(time.time() * 1000),
            ))

    def update(self, task_id: str, **fields):
        """Update mutable fields on a task. Ignores unknown fields."""
        sets = []
        vals = []
        for k, v in fields.items():
            if k not in ALLOWED_UPDATE_FIELDS:
                continue
            if k == 'mavis_raw' and v is not None and not isinstance(v, str):
                v = json.dumps(v, ensure_ascii=False)
            sets.append(f'{k} = ?')
            vals.append(v)
        if not sets:
            return
        sets.append('updated_at = ?')
        vals.append(int(time.time() * 1000))
        vals.append(task_id)
        with self._lock, self._conn() as conn:
            conn.execute(f'UPDATE tasks SET {", ".join(sets)} WHERE id = ?', vals)

    # ---------- reads ----------

    def get(self, task_id: str) -> Optional[Dict[str, Any]]:
        with self._conn() as conn:
            row = conn.execute('SELECT * FROM tasks WHERE id = ?', (task_id,)).fetchone()
            return self._row_to_dict(row) if row else None

    def list(self, status: Optional[str] = None, workspace: Optional[str] = None,
             limit: int = 50, since: Optional[int] = None) -> List[Dict[str, Any]]:
        """List tasks, newest first. All filters optional."""
        where = []
        vals = []
        if status:
            where.append('status = ?')
            vals.append(status)
        if workspace:
            where.append('workspace = ?')
            vals.append(workspace)
        if since is not None:
            where.append('created_at >= ?')
            vals.append(since)
        sql = 'SELECT * FROM tasks'
        if where:
            sql += ' WHERE ' + ' AND '.join(where)
        sql += ' ORDER BY created_at DESC LIMIT ?'
        vals.append(limit)
        with self._conn() as conn:
            rows = conn.execute(sql, vals).fetchall()
            return [self._row_to_dict(r) for r in rows]

    def count(self, status: Optional[str] = None) -> int:
        sql = 'SELECT COUNT(*) FROM tasks'
        vals = []
        if status:
            sql += ' WHERE status = ?'
            vals.append(status)
        with self._conn() as conn:
            return conn.execute(sql, vals).fetchone()[0]

    def stats(self) -> Dict[str, int]:
        """Get task counts by status."""
        with self._conn() as conn:
            rows = conn.execute(
                'SELECT status, COUNT(*) AS cnt FROM tasks GROUP BY status'
            ).fetchall()
            return {r['status']: r['cnt'] for r in rows}

    def delete_old(self, keep_count: int = 1000) -> int:
        """Delete oldest tasks keeping most recent N. Returns count deleted."""
        with self._lock, self._conn() as conn:
            cur = conn.execute('''
                DELETE FROM tasks WHERE id NOT IN (
                    SELECT id FROM tasks ORDER BY created_at DESC LIMIT ?
                )
            ''', (keep_count,))
            return cur.rowcount

    @staticmethod
    def _row_to_dict(row) -> Dict[str, Any]:
        d = dict(row)
        if 'files' in d and isinstance(d['files'], str):
            try:
                d['files'] = json.loads(d['files'])
            except Exception:
                d['files'] = []
        if 'mavis_raw' in d and isinstance(d['mavis_raw'], str):
            try:
                d['mavis_raw'] = json.loads(d['mavis_raw'])
            except Exception:
                pass
        return d


if __name__ == '__main__':
    # Self-test
    import tempfile
    test_db = os.path.join(tempfile.gettempdir(), 'acp-store-test.db')
    if os.path.exists(test_db):
        os.remove(test_db)
    store = TaskStore(test_db)
    print(f'DB: {test_db}')
    print(f'Stats: {store.stats()}')
    print(f'Count: {store.count()}')