"""
acp_tools.py — OpenClaw-native Python wrapper for OpenClaw ACP server
======================================================================
Provides direct Python access to ACP from any OpenClaw session.
No shell-out, no subprocess overhead. Just import and call.

Usage:
    from acp_tools import create_task, wait_task, stream_task, health

For full docs see SKILL.md in this directory.
"""
import sys
import os
from pathlib import Path
from typing import Optional, Callable, Iterator, Dict, Any, List

# Make acp_client importable. Try multiple locations:
#   1. Same project sibling: D:\openclaw-acp\client\
#   2. Original OpenClaw install: %USERPROFILE%\.openclaw\skills\mavis-coding\
_HERE = Path(__file__).parent.resolve()
_CANDIDATES = [
    str(_HERE.parent / 'client'),                              # packaged project
    r'%USERPROFILE%\.openclaw\skills\mavis-coding',            # original install
]
for _p in _CANDIDATES:
    _expanded = os.path.expandvars(_p)
    if os.path.isdir(_expanded) and os.path.exists(os.path.join(_expanded, 'acp_client.py')):
        sys.path.insert(0, _expanded)
        break

from acp_client import ACPClient, ACPError, read_token_from_server


# ---------- Singleton client ----------

_default_client: Optional[ACPClient] = None


def _client() -> ACPClient:
    global _default_client
    if _default_client is None:
        _default_client = ACPClient()
    return _default_client


def reset_client():
    """Reset the singleton client (e.g. if token rotated)."""
    global _default_client
    _default_client = None


# ---------- Health ----------

def health() -> dict:
    """GET /acp/health — server version + queue + db stats (no auth needed)."""
    return _client().health()


# ---------- Task creation ----------

def create_task(prompt: str, workspace: str, files: Optional[List[str]] = None,
                timeout: str = '5m') -> str:
    """POST /acp/task/create — enqueue a task. Returns task_id.

    Args:
        prompt: instruction for Mavis Coding
        workspace: directory the task runs in (must exist on disk)
        files: optional list of file paths to include
        timeout: '30s' / '5m' / etc (default 5m)

    Returns:
        task_id (str)
    """
    return _client().create_task(prompt, workspace, files=files, timeout=timeout)


# ---------- Task queries ----------

def get_task(task_id: str) -> dict:
    """GET /acp/task/get?id=X — fetch task state (cache first, falls back to SQLite)."""
    return _client().get_task(task_id)


def list_tasks(limit: int = 50) -> list:
    """GET /acp/task/list — recent in-memory tasks."""
    return _client().list_tasks(limit=limit)


def wait_task(task_id: str, timeout: float = 600, poll_interval: float = 2.0) -> dict:
    """Poll task until terminal state. Returns final task dict."""
    return _client().wait_for_task(task_id, timeout=timeout, poll_interval=poll_interval)


def cancel_task(task_id: str) -> dict:
    """POST /acp/task/cancel — cancel running/queued task."""
    return _client().cancel_task(task_id)


# ---------- SSE streaming ----------

def stream_task(task_id: str, on_event: Optional[Callable[[str, dict], None]] = None,
                max_idle_sec: float = 30.0) -> Iterator[dict]:
    """Subscribe to task SSE events. Yields dicts with 'type' + 'data'."""
    return _client().stream_task(task_id, on_event=on_event, max_idle_sec=max_idle_sec)


def run_and_stream(prompt: str, workspace: str, files: Optional[List[str]] = None,
                   timeout: str = '5m',
                   on_event: Optional[Callable[[str, dict], None]] = None,
                   max_idle_sec: float = 30.0) -> dict:
    """Convenience: create + stream + return final task dict."""
    return _client().run_and_stream(
        prompt=prompt, workspace=workspace, files=files,
        timeout=timeout, on_event=on_event, max_idle_sec=max_idle_sec,
    )


# ---------- History & stats (SQLite) ----------

def history(status: Optional[str] = None, workspace: Optional[str] = None,
            limit: int = 50, since: Optional[int] = None) -> list:
    """Query SQLite task history with optional filters.

    Args:
        status: 'succeeded' / 'failed' / 'timeout' / 'cancelled' / 'queued' / 'running'
        workspace: exact path match
        limit: 1-500 (default 50)
        since: unix timestamp ms (e.g. int(time.time()*1000) - 86400000 for last 24h)
    """
    return _client()._store.list(status=status, workspace=workspace, limit=limit, since=since)


def stats() -> dict:
    """Task counts by status + queue info."""
    # stats endpoint returns total + by_status + cache_size + queue_size + max_concurrent
    h = _client().health()
    return {
        'total': h.get('db', {}).get('total_tasks', 0),
        'by_status': h.get('db', {}).get('by_status', {}),
        'cache_size': h.get('cache_size', 0),
        'queue_size': h.get('queue', {}).get('size', 0),
        'active': h.get('queue', {}).get('active', 0),
        'max_concurrent': h.get('queue', {}).get('max_concurrent', 3),
    }


# ---------- Module self-test ----------

if __name__ == '__main__':
    print('=== acp_tools self-test ===')
    h = health()
    print(f'Server: {h.get("version")} (port {h.get("port")})')
    print(f'Queue: size={h.get("queue", {}).get("size")} active={h.get("queue", {}).get("active")}')
    print(f'DB: total={h.get("db", {}).get("total_tasks")}')
    print(f'WS: port={h.get("ws", {}).get("port")} active_conns={h.get("ws", {}).get("active_connections")}')
    print(f'[OK] acp_tools ready')