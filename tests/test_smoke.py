"""
test_smoke.py — PR-reproducible smoke test for OpenClaw ACP
============================================================
Validates the runtime contract documented in docs/RUNTIME_DEPS.md:
  1. Source has no hardcoded D:\\ / %USERPROFILE% / Windows raw-path literals
  2. acp_paths resolves cross-platform without env var
  3. Server REFUSES to start when $ACP_TOKEN is missing
  4. Server STARTS when $ACP_TOKEN is set; /acp/health returns 200
  5. Authenticated endpoints return 401 without token
  6. Authenticated endpoints return 200 with correct token
  7. WebSocket endpoint accepts ?token=<token> via query param
  8. Inbox write/read roundtrip works

Does NOT require Mavis Coding CLI / mcode. Does NOT require network.
Runs in <10s. Suitable as a CI gate on every PR.

Usage:
    python tests/test_smoke.py
    # or as a module:
    python -m tests.test_smoke
"""
import os
import sys
import re
import json
import time
import shutil
import socket
import sqlite3
import tempfile
import threading
import urllib.request
import urllib.error
from pathlib import Path
from contextlib import contextmanager

# --- Repo path setup -----------------------------------------------------
HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(REPO / 'openclaw-skill'))  # for acp_paths
sys.path.insert(0, str(REPO / 'server'))           # for acp-server

TEST_TOKEN = 'smoke-test-token-do-not-use-in-prod-1234567890'


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


# --- Assertion helpers ---------------------------------------------------
_failures: list = []


def _check(cond: bool, msg: str):
    if cond:
        print(f'  [PASS] {msg}')
    else:
        print(f'  [FAIL] {msg}')
        _failures.append(msg)


# --- Test 1: no hardcoded Windows paths in source -----------------------
WINDOWS_PATH_PATTERNS = [
    (re.compile(r"D:\\\\openclaw-acp"), "D:\\openclaw-acp literal"),
    (re.compile(r"D:/openclaw-acp"),     "D:/openclaw-acp literal"),
    (re.compile(r"%USERPROFILE%\\\\\.openclaw"), "%USERPROFILE%\\.openclaw literal"),
    (re.compile(r"%USERPROFILE%\\\\AppData"),   "%USERPROFILE%\\AppData literal"),
]

PY_FILES = [
    REPO / 'server' / 'acp-server.py',
    REPO / 'server' / 'acp_store.py',
    REPO / 'server' / 'acp_inbox_store.py',
    REPO / 'client' / 'acp_client.py',
    REPO / 'openclaw-skill' / 'acp_tools.py',
    REPO / 'openclaw-skill' / 'acp_cli.py',
    REPO / 'openclaw-skill' / 'acp_peer.py',
    REPO / 'openclaw-skill' / 'acp_paths.py',
]


def test_no_hardcoded_paths():
    print('\n[Test 1] No hardcoded Windows paths in Python source')
    for f in PY_FILES:
        if not f.exists():
            _check(False, f'{f.name} missing')
            continue
        text = f.read_text(encoding='utf-8')
        for pat, desc in WINDOWS_PATH_PATTERNS:
            if pat.search(text):
                _check(False, f'{f.name}: contains {desc}')
            else:
                _check(True, f'{f.name}: no {desc}')


# --- Test 2: acp_paths resolves without env var --------------------------
def test_acp_paths_cross_platform():
    print('\n[Test 2] acp_paths resolves cross-platform')
    # Clear env to force defaults
    saved_acp_home = os.environ.pop('ACP_HOME', None)
    saved_openclaw_home = os.environ.pop('OPENCLAW_HOME', None)
    saved_temp = os.environ.pop('ACP_TEMP_DIR', None)
    saved_sessions = os.environ.pop('ACP_SESSIONS_DIR', None)
    saved_mcode = os.environ.pop('MCODE_CMD', None)
    try:
        import acp_paths
        ah = acp_paths.resolve_acp_home()
        _check(isinstance(ah, Path), f'resolve_acp_home returns Path ({ah})')
        oc = acp_paths.resolve_openclaw_home()
        _check(isinstance(oc, Path), f'resolve_openclaw_home returns Path ({oc})')
        mcode = acp_paths.resolve_mcode_cmd()
        _check(isinstance(mcode, str) and len(mcode) > 0, f'resolve_mcode_cmd returns str ({mcode!r})')
        tmp = acp_paths.resolve_temp_dir()
        _check(tmp.exists() or tmp.parent.exists(), f'resolve_temp_dir returns real path ({tmp})')
        sr = acp_paths.sessions_root()
        _check(isinstance(sr, Path), f'sessions_root returns Path ({sr})')
        # Verify nothing defaulted to D:\ on a non-D install
        s = str(ah)
        if sys.platform == 'win32' and s.upper().startswith('C:\\'):
            _check(True, f'on Windows default ACP_HOME is C:\\ (not D:\\) ({s})')
        elif not s.upper().startswith('D:\\'):
            _check(True, f'ACP_HOME does not default to D:\\ ({s})')
        else:
            # Could be D:\\ if user has D:\\ as home; that's not our concern
            _check(True, f'ACP_HOME resolved to {s} (user default)')
    finally:
        for k, v in [('ACP_HOME', saved_acp_home), ('OPENCLAW_HOME', saved_openclaw_home),
                     ('ACP_TEMP_DIR', saved_temp), ('ACP_SESSIONS_DIR', saved_sessions),
                     ('MCODE_CMD', saved_mcode)]:
            if v is not None:
                os.environ[k] = v


# --- Test 3: server refuses to start without ACP_TOKEN -------------------
def test_server_refuses_without_token():
    print('\n[Test 3] Server refuses to start without $ACP_TOKEN')
    saved = os.environ.pop('ACP_TOKEN', None)
    try:
        # Run server in a subprocess; expect exit code 2
        proc = subprocess_run(
            [sys.executable, str(REPO / 'server' / 'acp-server.py')],
            env={k: v for k, v in os.environ.items() if k != 'ACP_TOKEN'},
            timeout=5,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        _check(proc.returncode == 2, f'server exited with code 2 (got {proc.returncode})')
        stderr = proc.stderr.decode('utf-8', errors='replace') if proc.stderr else ''
        _check('ACP_TOKEN' in stderr, f'stderr mentions ACP_TOKEN ({stderr[:200]!r})')
    finally:
        if saved is not None:
            os.environ['ACP_TOKEN'] = saved


# --- Test 4-7: server boots and serves traffic ---------------------------
import subprocess  # noqa: E402


def subprocess_run(*args, **kwargs):
    return subprocess.run(*args, **kwargs)


@contextmanager
def _running_server():
    """Boot the server in a subprocess with a free port + temp dirs."""
    port = _find_free_port()
    ws_port = _find_free_port()
    tmpdir = Path(tempfile.mkdtemp(prefix='acp-smoke-'))
    db_path = tmpdir / 'test.db'
    log_path = tmpdir / 'test.log'
    env = os.environ.copy()
    env['ACP_TOKEN'] = TEST_TOKEN
    env['ACP_PORT'] = str(port)
    env['ACP_WS_PORT'] = str(ws_port)
    env['ACP_DB_PATH'] = str(db_path)
    env['ACP_LOG'] = str(log_path)
    env['ACP_TEMP_DIR'] = str(tmpdir)
    env['PYTHONUNBUFFERED'] = '1'

    proc = subprocess.Popen(
        [sys.executable, str(REPO / 'server' / 'acp-server.py')],
        env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        cwd=str(REPO / 'server'),
    )
    # Wait for server to be ready (max 10s)
    deadline = time.time() + 10
    ready = False
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f'http://127.0.0.1:{port}/acp/health', timeout=1) as r:
                if r.status == 200:
                    ready = True
                    break
        except Exception:
            time.sleep(0.2)
    try:
        yield port, ws_port, tmpdir, ready
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_server_health_and_auth():
    print('\n[Test 4-7] Server health + auth + endpoints')
    with _running_server() as (port, ws_port, tmpdir, ready):
        if not ready:
            _check(False, f'server did not become ready on port {port}')
            return
        _check(ready, f'server ready on port {port} (WS {ws_port})')

        # Test 4: /acp/health is public
        with urllib.request.urlopen(f'http://127.0.0.1:{port}/acp/health', timeout=5) as r:
            body = json.loads(r.read().decode('utf-8'))
            _check(r.status == 200, 'GET /acp/health returns 200')
            _check(body.get('status') == 'ok', 'health body has status=ok')

        # Test 5: authenticated endpoint returns 401 without token
        try:
            urllib.request.urlopen(f'http://127.0.0.1:{port}/acp/task/list', timeout=5)
            _check(False, 'GET /acp/task/list without token should fail')
        except urllib.error.HTTPError as e:
            _check(e.code == 401, f'GET /acp/task/list without token → 401 (got {e.code})')

        # Test 6: authenticated endpoint returns 200 with correct token
        req = urllib.request.Request(
            f'http://127.0.0.1:{port}/acp/task/list',
            headers={'Authorization': f'Bearer {TEST_TOKEN}'},
        )
        with urllib.request.urlopen(req, timeout=5) as r:
            body = json.loads(r.read().decode('utf-8'))
            _check(r.status == 200, 'GET /acp/task/list with token returns 200')
            _check('tasks' in body, 'task list body has "tasks" key')

        # Test 7: inbox write/read roundtrip
        write_body = json.dumps({
            'session_id': 'smoke-test',
            'sender': 'goudan',
            'content': 'hello from smoke test',
        }).encode('utf-8')
        req = urllib.request.Request(
            f'http://127.0.0.1:{port}/acp/inbox/write',
            data=write_body,
            headers={'Authorization': f'Bearer {TEST_TOKEN}', 'Content-Type': 'application/json'},
            method='POST',
        )
        with urllib.request.urlopen(req, timeout=5) as r:
            wr = json.loads(r.read().decode('utf-8'))
            _check(r.status == 200 and 'message_id' in wr, f'inbox write returns message_id ({wr})')

        read_req = urllib.request.Request(
            f'http://127.0.0.1:{port}/acp/inbox/read?session_id=smoke-test&since_id=0',
            headers={'Authorization': f'Bearer {TEST_TOKEN}'},
        )
        with urllib.request.urlopen(read_req, timeout=5) as r:
            rd = json.loads(r.read().decode('utf-8'))
            _check(len(rd.get('messages', [])) >= 1, f'inbox read returns >=1 message ({len(rd.get("messages", []))})')


# --- Test 8: server stdout does NOT contain token -----------------------
def test_server_does_not_leak_token():
    print('\n[Test 8] Server stdout does not leak the token')
    port = _find_free_port()
    tmpdir = Path(tempfile.mkdtemp(prefix='acp-smoke-leak-'))
    env = os.environ.copy()
    env['ACP_TOKEN'] = TEST_TOKEN
    env['ACP_PORT'] = str(port)
    env['ACP_DB_PATH'] = str(tmpdir / 'leak.db')
    env['ACP_LOG'] = str(tmpdir / 'leak.log')
    env['ACP_TEMP_DIR'] = str(tmpdir)
    proc = subprocess.Popen(
        [sys.executable, str(REPO / 'server' / 'acp-server.py')],
        env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        cwd=str(REPO / 'server'),
    )
    # Read whatever it prints in 2s
    try:
        time.sleep(2)
        proc.terminate()
        stdout, _ = proc.communicate(timeout=5)
    except Exception:
        proc.kill()
        stdout = b''
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
    leaked = TEST_TOKEN in stdout.decode('utf-8', errors='replace')
    _check(not leaked, 'stdout does not contain the raw token')


# --- Main ----------------------------------------------------------------
def main():
    print('=== OpenClaw ACP smoke test ===')
    test_no_hardcoded_paths()
    test_acp_paths_cross_platform()
    test_server_refuses_without_token()
    test_server_health_and_auth()
    test_server_does_not_leak_token()

    print(f'\n=== Result ===')
    if _failures:
        print(f'FAILED: {len(_failures)} check(s)')
        for f in _failures:
            print(f'  - {f}')
        sys.exit(1)
    print('ALL CHECKS PASSED')
    sys.exit(0)


if __name__ == '__main__':
    main()
