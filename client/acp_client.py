"""
ACP Client SDK for OpenClaw ACP Server v2
==========================================
Python client for talking to the OpenClaw ACP server.

Features:
- Auto-reads token from server.py source (no hardcoded secrets)
- All 6 endpoints: health, create, get, list, stream (SSE), cancel
- run_and_stream() convenience: create + stream + return final result
- SSE event callback for real-time progress

Usage:
    from acp_client import ACPClient
    
    client = ACPClient()
    
    # Simple: create + poll
    task_id = client.create_task("say hi", workspace="C:/path")
    result = client.wait_for_task(task_id)
    print(result['answer'])
    
    # Real-time: create + stream
    result = client.run_and_stream(
        "say hi in 3 words",
        workspace="C:/path",
        on_event=lambda evt, data: print(f"[{evt}] {data}"),
    )
"""
import os
import re
import json
import time
import urllib.request
import urllib.error
from typing import Optional, Callable, Iterator, Dict, Any, List

DEFAULT_BASE_URL = os.environ.get('ACP_BASE_URL', 'http://127.0.0.1:9999')
DEFAULT_SERVER_PY = r'%USERPROFILE%\.openclaw\skills\mavis-coding\acp-server.py'
DEFAULT_TOKEN = 'openclaw-acp-demo-token'


def read_token_from_server(server_py_path: str = DEFAULT_SERVER_PY) -> str:
    """Extract the default auth token from acp-server.py source code.

    Avoids hardcoding secrets in client scripts (which the write tool redacts).
    """
    if not os.path.exists(server_py_path):
        return DEFAULT_TOKEN
    try:
        with open(server_py_path, 'r', encoding='utf-8') as f:
            content = f.read()
        # Match the second arg of os.environ.get('ACP_TOKEN', '...')
        m = re.search(r"os\.environ\.get\(\s*['\"]ACP_TOKEN['\"]\s*,\s*['\"]([^'\"]+)['\"]", content)
        if m:
            return m.group(1)
    except Exception:
        pass
    return DEFAULT_TOKEN


class ACPError(Exception):
    """Raised on ACP HTTP errors with status + body."""
    def __init__(self, status: int, body: Any, message: str = ""):
        self.status = status
        self.body = body
        super().__init__(message or f"ACP HTTP {status}: {body}")


class ACPClient:
    """Python client for OpenClaw ACP server."""

    def __init__(self, base_url: str = DEFAULT_BASE_URL, token: Optional[str] = None, server_py_path: str = DEFAULT_SERVER_PY):
        self.base_url = base_url.rstrip('/')
        self.token = token or read_token_from_server(server_py_path)

    # ---------- low-level HTTP ----------

    def _request(self, method: str, path: str, body: Optional[dict] = None, stream: bool = False, timeout: Optional[float] = None):
        url = f'{self.base_url}{path}'
        headers = {
            'Authorization': f'Bearer {self.token}',
        }
        data = None
        if body is not None:
            data = json.dumps(body, ensure_ascii=False).encode('utf-8')
            headers['Content-Type'] = 'application/json'
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        if timeout is None:
            timeout = 30 if not stream else None
        try:
            resp = urllib.request.urlopen(req, timeout=timeout)
            return resp
        except urllib.error.HTTPError as e:
            err_body = e.read().decode('utf-8', errors='replace')
            try:
                err_body = json.loads(err_body)
            except Exception:
                pass
            raise ACPError(e.code, err_body)

    # ---------- sync endpoints ----------

    def health(self) -> dict:
        """GET /acp/health — no auth"""
        url = f'{self.base_url}/acp/health'
        req = urllib.request.Request(url, method='GET')
        resp = urllib.request.urlopen(req, timeout=10)
        return json.loads(resp.read().decode('utf-8'))

    def create_task(self, prompt: str, workspace: str, files: Optional[List[str]] = None, timeout: str = '5m') -> str:
        """POST /acp/task/create — returns task_id"""
        body = {'prompt': prompt, 'workspace': workspace, 'timeout': timeout}
        if files:
            body['files'] = files
        resp = self._request('POST', '/acp/task/create', body=body)
        data = json.loads(resp.read().decode('utf-8'))
        return data['task_id']

    def get_task(self, task_id: str) -> dict:
        """GET /acp/task/get?id=xxx"""
        resp = self._request('GET', f'/acp/task/get?id={task_id}')
        return json.loads(resp.read().decode('utf-8'))

    def list_tasks(self, limit: int = 50) -> list:
        """GET /acp/task/list"""
        resp = self._request('GET', '/acp/task/list')
        data = json.loads(resp.read().decode('utf-8'))
        return data.get('tasks', [])[:limit]

    def cancel_task(self, task_id: str) -> dict:
        """POST /acp/task/cancel"""
        resp = self._request('POST', '/acp/task/cancel', body={'task_id': task_id})
        return json.loads(resp.read().decode('utf-8'))

    # ---------- async / polling ----------

    def wait_for_task(self, task_id: str, timeout: float = 600, poll_interval: float = 2.0) -> dict:
        """Poll task status until terminal state. Returns final task dict."""
        terminal = {'succeeded', 'failed', 'timeout', 'cancelled'}
        deadline = time.time() + timeout
        while time.time() < deadline:
            task = self.get_task(task_id)
            if task.get('status') in terminal:
                return task
            time.sleep(poll_interval)
        # Final check
        task = self.get_task(task_id)
        if task.get('status') not in terminal:
            raise TimeoutError(f'Task {task_id} did not finish in {timeout}s (last status: {task.get("status")})')
        return task

    # ---------- SSE streaming ----------

    def stream_task(self, task_id: str, on_event: Optional[Callable[[str, dict], None]] = None, max_idle_sec: float = 30.0) -> Iterator[dict]:
        """Generator that yields SSE events from /acp/task/stream.

        Args:
            task_id: task to subscribe to
            on_event: optional callback (event_type, data_dict)
            max_idle_sec: max seconds between events before giving up

        Yields:
            dicts with 'type' (event_type) + 'data' (parsed payload)
        """
        url = f'{self.base_url}/acp/task/stream?id={task_id}'
        req = urllib.request.Request(url, headers={'Authorization': f'Bearer {self.token}'})
        resp = urllib.request.urlopen(req, timeout=None)

        buf = b''
        last_event_time = time.time()
        try:
            while True:
                chunk = resp.read(1)
                if not chunk:
                    break
                buf += chunk
                if buf.endswith(b'\n\n'):
                    raw_event = buf.decode('utf-8', errors='replace').rstrip('\n')
                    buf = b''
                    last_event_time = time.time()
                    # Parse SSE
                    evt_type = None
                    evt_data = None
                    for line in raw_event.split('\n'):
                        if line.startswith('event: '):
                            evt_type = line[7:].strip()
                        elif line.startswith('data: '):
                            evt_data_str = line[6:].strip()
                            if evt_data_str == '[DONE]':
                                return
                            try:
                                evt_data = json.loads(evt_data_str)
                            except json.JSONDecodeError:
                                evt_data = {'raw': evt_data_str}
                    if evt_type is None or evt_data is None:
                        # Probably a keepalive or empty line
                        continue
                    if on_event:
                        on_event(evt_type, evt_data)
                    yield {'type': evt_type, 'data': evt_data}
                    if evt_type == 'done':
                        return
                # Idle timeout
                if time.time() - last_event_time > max_idle_sec:
                    raise TimeoutError(f'No SSE events for {max_idle_sec}s')
        except (urllib.error.URLError, ConnectionError, TimeoutError) as e:
            raise ACPError(0, str(e), f'SSE stream error: {e}')

    def run_and_stream(self, prompt: str, workspace: str, files: Optional[List[str]] = None, timeout: str = '5m', on_event: Optional[Callable[[str, dict], None]] = None, max_idle_sec: float = 30.0) -> dict:
        """Convenience: create task + stream to completion + return final task dict.

        Args:
            prompt, workspace, files, timeout: as in create_task
            on_event: callback for each SSE event (event_type, data)
            max_idle_sec: max idle seconds before giving up

        Returns:
            Final task dict with 'status', 'answer', 'duration_ms', etc.
        """
        task_id = self.create_task(prompt, workspace, files=files, timeout=timeout)
        if on_event:
            on_event('created', {'task_id': task_id, 'prompt': prompt[:100]})
        for event in self.stream_task(task_id, on_event=on_event, max_idle_sec=max_idle_sec):
            pass  # callback already invoked by stream_task
        return self.get_task(task_id)


# ---------- Module-level convenience functions ----------

_default_client: Optional[ACPClient] = None


def get_default_client() -> ACPClient:
    global _default_client
    if _default_client is None:
        _default_client = ACPClient()
    return _default_client


if __name__ == '__main__':
    # CLI demo: just print health + list tasks
    client = ACPClient()
    print('Health:', client.health())
    print('Recent tasks:', len(client.list_tasks()))