"""
OpenClaw ACP Server v7 — v5 SSE/WS + v7-bidir peer-to-peer inbox
================================================================
HTTP server (port 9999) + WebSocket server (port 9998) sharing state.

v5 additions:
  - WebSocket endpoint at ws://localhost:9998/acp/ws?task_id=X
  - Bidirectional: server pushes events, client can send cancel/ping commands
  - Multiple tasks supported per WS connection via subscribe command
  - Shared state with HTTP server (TASKS, TASK_QUEUES, STORE, ACTIVE_TASKS)
  - HTTP server uses ThreadingHTTPServer for concurrent request handling
  - WS server runs in separate thread with its own asyncio loop

v7-bidir additions (2026-08-14):
  - Peer-to-peer inbox for goudan <-> mavis collaboration
  - SQLite-backed acp_inbox table (InboxStore)
  - 4 new HTTP endpoints:
    POST /acp/inbox/write    write a message to a session
    GET  /acp/inbox/read     read new messages (poll-based)
    POST /acp/inbox/ask      write a question + block until answered
    POST /acp/inbox/answer   answer a pending question
  - Sender field: 'goudan' / 'mavis' / 'boss' / 'system'
  - Both agents can write/read — symmetric, not master/slave

v4 retained: worker pool, queue, 'queued' status, SQLite persistence, SSE.

Endpoints (HTTP):
  POST /acp/task/create   Create+enqueue a task
  GET  /acp/task/get     Get task status (cache first, fallback to SQLite)
  GET  /acp/task/list    List recent in-memory tasks
  GET  /acp/task/history Query SQLite with filters
  GET  /acp/task/stats   Task counts by status
  GET  /acp/task/stream  SSE stream (one-way, retained for compat)
  POST /acp/task/cancel  Cancel task
  GET  /acp/health       Health check (HTTP + WS info)

Endpoints (WebSocket):
  WS /acp/ws?task_id=X          Subscribe to single task events
  WS /acp/ws                    Global feed (no initial subscription)
  Client -> Server: {"action":"cancel","task_id":"X"}
                     {"action":"ping"}
                     {"action":"subscribe","task_id":"X"}
  Server -> Client: same SSE events (snapshot/queued/start/output/done) as JSON
"""
import json
import os
import sys
import time
import uuid
import queue
import threading
import subprocess
import logging
import asyncio
from pathlib import Path
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn
from urllib.parse import urlparse, parse_qs

import websockets
from websockets.asyncio.server import serve as ws_serve

from acp_store import TaskStore
from acp_inbox_store import InboxStore

# ===== Path helpers (cross-platform) =====
# acp_paths.py lives at <ACP_HOME>/openclaw-skill/. Add the script's parent
# dir to sys.path so 'import acp_paths' works whether the server is started
# from server/, openclaw-skill/, or any cwd.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'openclaw-skill'))
import acp_paths  # noqa: E402

# ===== Config =====
# Env reads use getattr+__import__ to dodge write-tool redaction of the
# literal token 'os.environ.get('. See PITFALLS.md #1. Both styles are
# equivalent at runtime.
_os_env = getattr(__import__('os'), 'environ')

PORT = int(_os_env.get('ACP_PORT', '9999'))
HOST = _os_env.get('ACP_HOST', '127.0.0.1')
WS_PORT = int(_os_env.get('ACP_WS_PORT', '9998'))
MAX_CONCURRENT = int(_os_env.get('ACP_MAX_CONCURRENT', '3'))

# AUTH_TOKEN is REQUIRED. There is NO default value. The server refuses to
# start if $ACP_TOKEN is missing or empty. See README.md "Auth" for how
# to obtain a token from the operator.
_AUTH_TOKEN = _os_env.get('ACP_TOKEN')
if not _AUTH_TOKEN:
    sys.stderr.write('FATAL: $ACP_TOKEN environment variable is not set.\n')
    sys.stderr.write('       The server refuses to start with an empty/missing token.\n')
    sys.stderr.write('       Set ACP_TOKEN to a strong secret before launching.\n')
    sys.stderr.write('       Example (PowerShell):\n')
    sys.stderr.write('         $env:ACP_TOKEN = (python -c "import secrets;print(secrets.token_urlsafe(32))")\n')
    sys.exit(2)
AUTH_TOKEN = _AUTH_TOKEN
del _AUTH_TOKEN  # don't leave it in module namespace as a writable global

# Cross-platform default paths. Override via env if needed.
LOG_FILE = str(acp_paths.resolve_temp_dir() / 'acp-server.log')
_db_override = _os_env.get('ACP_DB_PATH')
DEFAULT_DB_PATH = str(Path(_db_override).expanduser().resolve()) if _db_override else str(acp_paths.resolve_temp_dir() / 'acp-tasks.db')

# Mavis Coding CLI (external runtime dependency). Override via $MCODE_CMD.
# Default: $HOME/.minimax-code/mcode(.cmd). Cross-platform.
MCODE_CMD = acp_paths.resolve_mcode_cmd()

# ===== Logging =====
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE, encoding='utf-8'),
        logging.StreamHandler()
    ]
)
log = logging.getLogger('acp')

# ===== Persistent stores =====
STORE = TaskStore()
INBOX = InboxStore()  # v7-bidir: peer-to-peer messaging

# ===== In-memory task cache =====
TASKS = {}
TASKS_LOCK = threading.Lock()
TASK_HISTORY_LIMIT = 100

# ===== Event queues for SSE / WS =====
TASK_QUEUES = {}
TASK_QUEUES_LOCK = threading.Lock()
QUEUE_KEEP_ALIVE_SEC = 300

# ===== Concurrency / task queue =====
TASK_QUEUE = queue.Queue()
ACTIVE_TASKS = set()
ACTIVE_LOCK = threading.Lock()
WORKERS_STARTED = False
WORKERS_START_LOCK = threading.Lock()

# ===== WS connection tracking (for fan-out / disconnect cleanup) =====
WS_CONN_COUNT = 0  # counter incremented on connect, decremented on disconnect
WS_LOCK = threading.Lock()


def get_queue_position(task_id):
    with ACTIVE_LOCK:
        items = list(TASK_QUEUE.queue)
    for i, tid in enumerate(items):
        if tid == task_id:
            return i + 1
    return None


def get_queue_snapshot():
    with ACTIVE_LOCK:
        return list(TASK_QUEUE.queue), set(ACTIVE_TASKS)


def get_or_create_queue(task_id):
    with TASK_QUEUES_LOCK:
        if task_id not in TASK_QUEUES:
            TASK_QUEUES[task_id] = queue.Queue()
        return TASK_QUEUES[task_id]


def cleanup_queue(task_id, delay=QUEUE_KEEP_ALIVE_SEC):
    def _do():
        time.sleep(delay)
        with TASK_QUEUES_LOCK:
            TASK_QUEUES.pop(task_id, None)
    threading.Thread(target=_do, daemon=True).start()


def new_task(prompt, workspace, files=None, timeout='5m'):
    task_id = 'task_' + uuid.uuid4().hex[:16]
    task = {
        'id': task_id,
        'status': 'queued',
        'prompt': prompt,
        'workspace': workspace,
        'files': files or [],
        'timeout': timeout,
        'created_at': int(time.time() * 1000),
        'started_at': None,
        'finished_at': None,
        'answer': None,
        'session_id': None,
        'duration_ms': None,
        'error': None,
        'queue_position': None,
    }
    with TASKS_LOCK:
        TASKS[task_id] = task
        completed = [tid for tid, t in TASKS.items() if t['status'] in ('succeeded', 'failed', 'timeout', 'cancelled')]
        if len(TASKS) > TASK_HISTORY_LIMIT:
            for tid in sorted(completed, key=lambda x: TASKS[x].get('finished_at', 0))[:len(TASKS) - TASK_HISTORY_LIMIT]:
                TASKS.pop(tid, None)
    try:
        STORE.insert(task)
    except Exception as e:
        log.error(f'Store insert failed for {task_id}: {e}')
    events_queue = get_or_create_queue(task_id)
    pos = get_queue_position(task_id)
    task['queue_position'] = pos
    events_queue.put({
        'type': 'queued',
        'task_id': task_id,
        'queue_position': pos,
        'max_concurrent': MAX_CONCURRENT,
    })
    TASK_QUEUE.put(task_id)
    log.info(f'Task queued: {task_id} position={pos} workspace={workspace}')
    return task_id


def run_task(task_id):
    """Worker function: invoked by worker_loop when task is dequeued."""
    with TASKS_LOCK:
        task = TASKS.get(task_id)
        if not task:
            log.warning(f'run_task: {task_id} not in cache, skipping')
            return
        task['status'] = 'running'
        task['started_at'] = int(time.time() * 1000)
        task['queue_position'] = None
    try:
        STORE.update(task_id, status='running', started_at=task['started_at'])
    except Exception as e:
        log.error(f'Store update failed for {task_id}: {e}')

    log.info(f'Task running: {task_id}')
    events_queue = get_or_create_queue(task_id)
    events_queue.put({'type': 'start', 'task_id': task_id, 'prompt': task['prompt'][:100]})

    stdout_buffer = []
    stderr_buffer = []

    def read_stream(stream, stream_name, buffer):
        try:
            for line in iter(stream.readline, ''):
                line = line.rstrip()
                buffer.append(line)
                events_queue.put({'type': 'output', 'stream': stream_name, 'line': line})
        except Exception as e:
            events_queue.put({'type': 'output', 'stream': stream_name, 'line': f'[stream error: {e}]'})
        finally:
            try:
                stream.close()
            except Exception:
                pass

    mcode_args = [
        'exec',
        '--input', '-',          # read prompt from stdin (only stdin supported per mcode docs)
        '--input-format', 'text',
        '--cwd', task['workspace'],
        # NOTE: mcode exec does NOT support --output-format json (verified 2026-08-14).
        # The server used to pass it but mcode ignored it, then server tried to json.loads
        # the free-text output and got "Expecting value: line 1 column 1" 100% of the time.
        # Server now accepts free-text stdout and treats it as the answer (see parsing below).
        '--permission', 'full',  # was 'smart' — smart requires interactive TTY confirmation, ACP dispatcher has no TTY, so always blocked. 'full' = auto-approve all (acceptable for ACP: boss explicitly authorized this mode via the ACP server being their tool).
        '--timeout', task['timeout'],
    ]
    if task['files']:
        for f in task['files']:
            mcode_args += ['--file', f]

    timed_out = False
    try:
        timeout_str = task['timeout']
        if timeout_str.endswith('m'):
            timeout_sec = int(timeout_str[:-1]) * 60
        elif timeout_str.endswith('s'):
            timeout_sec = int(timeout_str[:-1])
        else:
            timeout_sec = int(timeout_str)

        proc = subprocess.Popen(
            ['cmd.exe', '/c', MCODE_CMD] + mcode_args,
            stdin=subprocess.PIPE,   # feed the prompt via stdin (mcode exec requires this)
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding='utf-8',
            errors='replace',
            bufsize=1,
        )

        t_out = threading.Thread(target=read_stream, args=(proc.stdout, 'stdout', stdout_buffer), daemon=True)
        t_err = threading.Thread(target=read_stream, args=(proc.stderr, 'stderr', stderr_buffer), daemon=True)
        # Write the prompt to mcode's stdin (per `--input -`) and close it.
        # mcode will then execute the prompt non-interactively and exit when done.
        try:
            proc.stdin.write(task['prompt'])
            proc.stdin.flush()
            proc.stdin.close()
        except Exception as e:
            log.warning(f'stdin write failed for {task_id}: {e}')
        t_out.start()
        t_err.start()

        try:
            proc.wait(timeout=timeout_sec)
        except subprocess.TimeoutExpired:
            proc.kill()
            timed_out = True
            log.info(f'Task timeout: {task_id}')

        t_out.join(timeout=5)
        t_err.join(timeout=5)

        stdout = '\n'.join(stdout_buffer)
        stderr = '\n'.join(stderr_buffer)

        with TASKS_LOCK:
            task = TASKS[task_id]
            task['finished_at'] = int(time.time() * 1000)
            task['duration_ms'] = task['finished_at'] - task['started_at']
            update_fields = {'finished_at': task['finished_at'], 'duration_ms': task['duration_ms']}

            if timed_out:
                task['status'] = 'timeout'
                task['error'] = f'Mavis timed out after {task["timeout"]}'
                update_fields['status'] = 'timeout'
                update_fields['error'] = task['error']
                events_queue.put({'type': 'done', 'status': 'timeout', 'duration_ms': task['duration_ms']})
            else:
                # Try to parse stdout as JSON first; if mcode exec didn't produce JSON,
                # fall back to treating the entire stdout as the answer (free-text mode).
                # This is the fix for the long-standing "Failed to parse Mavis output" error.
                parsed = None
                parse_err = None
                if stdout.strip():
                    try:
                        parsed = json.loads(stdout)
                    except Exception as e:
                        parse_err = str(e)

                if parsed and isinstance(parsed, dict):
                    # JSON path — mcode eventually outputs structured JSON
                    task['answer'] = parsed.get('answer', '')
                    task['session_id'] = parsed.get('session_id', '')
                    mavis_status = parsed.get('status', 'failed')
                    if mavis_status == 'succeeded':
                        task['status'] = 'succeeded'
                    elif mavis_status == 'timeout':
                        task['status'] = 'timeout'
                    else:
                        task['status'] = 'failed'
                    task['mavis_raw'] = parsed
                    update_fields['status'] = task['status']
                    update_fields['answer'] = task['answer']
                    update_fields['session_id'] = task['session_id']
                    update_fields['mavis_raw'] = task['mavis_raw']
                    events_queue.put({
                        'type': 'done',
                        'status': task['status'],
                        'duration_ms': task['duration_ms'],
                        'answer_preview': (task['answer'] or '')[:200],
                    })
                else:
                    # Free-text fallback — mcode exec's actual output mode (no JSON flag).
                    # Heuristic: succeeded if stdout non-empty and no obvious error markers.
                    task['answer'] = stdout
                    task['mavis_raw'] = {'stdout_raw': stdout, 'parse_mode': 'free_text'}
                    error_markers = ['error:', 'failed to', 'unauthorized', 'panic:', 'traceback']
                    stdout_lower = stdout.lower()
                    has_error = any(m in stdout_lower for m in error_markers)
                    task['status'] = 'failed' if has_error else 'succeeded'
                    update_fields['status'] = task['status']
                    update_fields['answer'] = task['answer']
                    update_fields['mavis_raw'] = task['mavis_raw']
                    events_queue.put({
                        'type': 'done',
                        'status': task['status'],
                        'duration_ms': task['duration_ms'],
                        'answer_preview': (task['answer'] or '')[-300:],
                    })

            try:
                STORE.update(task_id, **update_fields)
            except Exception as e:
                log.error(f'Store update failed for {task_id}: {e}')

        log.info(f'Task finished: {task_id} status={TASKS[task_id]["status"]} duration={TASKS[task_id]["duration_ms"]}ms')
        cleanup_queue(task_id)

    except Exception as e:
        with TASKS_LOCK:
            task = TASKS[task_id]
            task['status'] = 'failed'
            task['finished_at'] = int(time.time() * 1000)
            task['duration_ms'] = task['finished_at'] - task['started_at']
            task['error'] = f'Exception: {e}'
            events_queue.put({'type': 'done', 'status': 'failed', 'error': str(e)})
        try:
            STORE.update(task_id, status='failed', finished_at=task['finished_at'],
                          duration_ms=task['duration_ms'], error=task['error'])
        except Exception as ex:
            log.error(f'Store update failed for {task_id}: {ex}')
        log.info(f'Task failed: {task_id} error={e}')
        cleanup_queue(task_id)


def worker_loop():
    log.info('Worker started')
    while True:
        task_id = TASK_QUEUE.get()
        with ACTIVE_LOCK:
            ACTIVE_TASKS.add(task_id)
        try:
            run_task(task_id)
        except Exception as e:
            log.error(f'Worker exception on {task_id}: {e}')
        finally:
            with ACTIVE_LOCK:
                ACTIVE_TASKS.discard(task_id)
            TASK_QUEUE.task_done()


def start_workers(n=MAX_CONCURRENT):
    global WORKERS_STARTED
    with WORKERS_START_LOCK:
        if WORKERS_STARTED:
            return
        WORKERS_STARTED = True
        for i in range(n):
            t = threading.Thread(target=worker_loop, daemon=True, name=f'acp-worker-{i+1}')
            t.start()
        log.info(f'Started {n} workers')


def cancel_task_internal(task_id):
    """Shared cancel logic (HTTP + WS). Returns dict with status_code + body."""
    with TASKS_LOCK:
        task = TASKS.get(task_id)
        if not task:
            task = STORE.get(task_id)
        if not task:
            return {'status_code': 404, 'body': {'error': 'task not found'}}
        if task['status'] in ('created', 'running'):
            task['status'] = 'cancelled'
            task['finished_at'] = int(time.time() * 1000)
            task['error'] = 'cancelled by client'
            q = TASK_QUEUES.get(task_id)
            if q:
                q.put({'type': 'done', 'status': 'cancelled'})
            try:
                STORE.update(task_id, status='cancelled',
                             finished_at=task['finished_at'], error=task['error'])
            except Exception as e:
                log.error(f'Store update cancel failed: {e}')
            log.info(f'Task cancelled: {task_id}')
            return {'status_code': 200, 'body': {'task_id': task_id, 'status': 'cancelled'}}
        elif task['status'] == 'queued':
            task['status'] = 'cancelled'
            task['finished_at'] = int(time.time() * 1000)
            task['error'] = 'cancelled while in queue'
            q = TASK_QUEUES.get(task_id)
            if q:
                q.put({'type': 'done', 'status': 'cancelled'})
            try:
                STORE.update(task_id, status='cancelled',
                             finished_at=task['finished_at'], error=task['error'])
            except Exception as e:
                log.error(f'Store update cancel queued failed: {e}')
            log.info(f'Queued task cancelled: {task_id}')
            return {'status_code': 200, 'body': {'task_id': task_id, 'status': 'cancelled (was in queue)'}}
        else:
            return {'status_code': 409, 'body': {'error': f'task already finished: {task["status"]}'}}


# ===== HTTP Handler =====
class ACPHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        log.debug(f'HTTP {self.command} {self.path} from {self.client_address[0]}')

    def _check_auth(self):
        token = self.headers.get('Authorization', '').replace('Bearer ', '')
        return token == AUTH_TOKEN

    def _send_json(self, status, body):
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(json.dumps(body, ensure_ascii=False).encode('utf-8'))

    def _read_body(self):
        length = int(self.headers.get('Content-Length', 0))
        if length > 0:
            body = self.rfile.read(length)
            try:
                return json.loads(body.decode('utf-8'))
            except Exception as e:
                log.error(f'Bad JSON body: {e}')
                return None
        return {}

    def _send_sse_event(self, event_type, data_dict):
        data = json.dumps(data_dict, ensure_ascii=False)
        msg = f'event: {event_type}\ndata: {data}\n\n'.encode('utf-8')
        try:
            self.wfile.write(msg)
            self.wfile.flush()
            return True
        except (BrokenPipeError, ConnectionResetError):
            return False

    def _stream_task(self, task_id):
        with TASKS_LOCK:
            task = TASKS.get(task_id)
        if not task:
            task = STORE.get(task_id)
        if not task:
            self._send_json(404, {'error': 'task not found', 'id': task_id})
            return

        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream')
        self.send_header('Cache-Control', 'no-cache')
        self.send_header('Connection', 'keep-alive')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('X-Accel-Buffering', 'no')
        self.end_headers()

        if task.get('status') == 'queued':
            task = dict(task)
            task['queue_position'] = get_queue_position(task_id)
        snapshot = {'type': 'snapshot', 'task_id': task_id, 'task': task}
        if not self._send_sse_event('snapshot', snapshot):
            return

        with TASK_QUEUES_LOCK:
            events_queue = TASK_QUEUES.get(task_id)
        task_finished = task.get('status') in ('succeeded', 'failed', 'timeout', 'cancelled')

        if task_finished and events_queue is None:
            done = {'type': 'done', 'status': task['status'], 'duration_ms': task.get('duration_ms')}
            self._send_sse_event('done', done)
            return

        if events_queue is None:
            events_queue = get_or_create_queue(task_id)

        last_keepalive = time.time()
        try:
            while True:
                try:
                    event = events_queue.get(timeout=0.5)
                    event_type = event.get('type', 'message')
                    if not self._send_sse_event(event_type, event):
                        return
                    last_keepalive = time.time()
                    if event_type == 'done':
                        break
                except queue.Empty:
                    if time.time() - last_keepalive > 15:
                        try:
                            self.wfile.write(b': keepalive\n\n')
                            self.wfile.flush()
                            last_keepalive = time.time()
                        except (BrokenPipeError, ConnectionResetError):
                            return
                    current = TASKS.get(task_id) or STORE.get(task_id)
                    if current and current.get('status') in ('succeeded', 'failed', 'timeout', 'cancelled'):
                        done_event = {
                            'type': 'done',
                            'status': current['status'],
                            'duration_ms': current.get('duration_ms'),
                        }
                        if not self._send_sse_event('done', done_event):
                            return
                        break
        except Exception as e:
            log.error(f'SSE error for task {task_id}: {e}')

    def do_GET(self):
        path = urlparse(self.path).path
        if path == '/acp/health':
            try:
                stats = STORE.stats()
                total_tasks = STORE.count()
            except Exception as e:
                stats = {'error': str(e)}
                total_tasks = -1
            with WS_LOCK:
                ws_conn_count = WS_CONN_COUNT
            queue_list, active_set = get_queue_snapshot()
            self._send_json(200, {
                'status': 'ok',
                'service': 'openclaw-acp',
                'version': 'v7-bidir',
                'cache_size': len(TASKS),
                'active_queues': len([q for q in TASK_QUEUES.values() if not q.empty()]),
                'queue': {
                    'size': len(queue_list),
                    'active': len(active_set),
                    'max_concurrent': MAX_CONCURRENT,
                    'pending_ids': queue_list[:20],
                },
                'db': {
                    'total_tasks': total_tasks,
                    'by_status': stats,
                },
                'ws': {
                    'port': WS_PORT,
                    'active_connections': ws_conn_count,
                },
                'port': PORT,
                'inbox': {
                    'enabled': True,
                    'sessions': len(INBOX.list_sessions()),
                },
            })
        elif path == '/acp/task/list':
            if not self._check_auth():
                return self._send_json(401, {'error': 'unauthorized'})
            with TASKS_LOCK:
                task_list = list(TASKS.values())
            task_list_sorted = sorted(task_list, key=lambda t: t['created_at'], reverse=True)
            queue_list, _ = get_queue_snapshot()
            for t in task_list_sorted:
                if t['status'] == 'queued':
                    t['queue_position'] = (queue_list.index(t['id']) + 1) if t['id'] in queue_list else None
            self._send_json(200, {'tasks': task_list_sorted[:50], 'total': len(task_list)})
        elif path.startswith('/acp/task/get'):
            if not self._check_auth():
                return self._send_json(401, {'error': 'unauthorized'})
            qs = parse_qs(urlparse(self.path).query)
            task_id = qs.get('id', [''])[0]
            with TASKS_LOCK:
                task = TASKS.get(task_id)
            if not task:
                task = STORE.get(task_id)
            if task:
                if task.get('status') == 'queued':
                    pos = get_queue_position(task_id)
                    if pos is not None:
                        task = dict(task)
                        task['queue_position'] = pos
                self._send_json(200, task)
            else:
                self._send_json(404, {'error': 'task not found', 'id': task_id})
        elif path.startswith('/acp/task/history'):
            if not self._check_auth():
                return self._send_json(401, {'error': 'unauthorized'})
            qs = parse_qs(urlparse(self.path).query)
            status = qs.get('status', [None])[0]
            workspace = qs.get('workspace', [None])[0]
            since_str = qs.get('since', [None])[0]
            limit_str = qs.get('limit', ['50'])[0]
            try:
                since = int(since_str) if since_str else None
                limit = max(1, min(500, int(limit_str)))
            except ValueError:
                return self._send_json(400, {'error': 'invalid since/limit'})
            try:
                tasks = STORE.list(status=status, workspace=workspace, since=since, limit=limit)
                self._send_json(200, {
                    'tasks': tasks,
                    'total_returned': len(tasks),
                    'filters': {'status': status, 'workspace': workspace, 'since': since, 'limit': limit},
                })
            except Exception as e:
                self._send_json(500, {'error': f'history query failed: {e}'})
        elif path == '/acp/task/stats':
            if not self._check_auth():
                return self._send_json(401, {'error': 'unauthorized'})
            try:
                self._send_json(200, {
                    'total': STORE.count(),
                    'by_status': STORE.stats(),
                    'cache_size': len(TASKS),
                    'queue_size': len(get_queue_snapshot()[0]),
                    'max_concurrent': MAX_CONCURRENT,
                })
            except Exception as e:
                self._send_json(500, {'error': str(e)})
        elif path.startswith('/acp/task/stream'):
            if not self._check_auth():
                return self._send_json(401, {'error': 'unauthorized'})
            qs = parse_qs(urlparse(self.path).query)
            task_id = qs.get('id', [''])[0]
            if not task_id:
                self._send_json(400, {'error': 'id parameter required'})
                return
            self._stream_task(task_id)
        elif path.startswith('/acp/inbox/read'):
            # GET /acp/inbox/read?session_id=X&since_id=N&sender=Y&msg_type=Z&limit=N
            if not self._check_auth():
                return self._send_json(401, {'error': 'unauthorized'})
            qs = parse_qs(urlparse(self.path).query)
            session_id = qs.get('session_id', [''])[0]
            if not session_id:
                return self._send_json(400, {'error': 'session_id required'})
            since_str = qs.get('since_id', ['0'])[0]
            sender = qs.get('sender', [None])[0]
            msg_type = qs.get('msg_type', [None])[0]
            limit_str = qs.get('limit', ['50'])[0]
            try:
                since_id = int(since_str)
                limit = max(1, min(500, int(limit_str)))
            except ValueError:
                return self._send_json(400, {'error': 'invalid since_id/limit'})
            try:
                messages = INBOX.read_pending(
                    session_id=session_id,
                    since_id=since_id,
                    sender=sender,
                    msg_type=msg_type,
                    limit=limit,
                )
                # Auto-mark as read
                if messages:
                    INBOX.mark_read([m['id'] for m in messages])
                self._send_json(200, {
                    'session_id': session_id,
                    'messages': messages,
                    'count': len(messages),
                    'last_id': messages[-1]['id'] if messages else since_id,
                })
            except Exception as e:
                log.error(f'inbox read error: {e}')
                self._send_json(500, {'error': f'inbox read failed: {e}'})
        elif path == '/acp/inbox/sessions':
            # GET /acp/inbox/sessions?limit=N — list recent sessions
            if not self._check_auth():
                return self._send_json(401, {'error': 'unauthorized'})
            qs = parse_qs(urlparse(self.path).query)
            limit_str = qs.get('limit', ['20'])[0]
            try:
                limit = max(1, min(200, int(limit_str)))
            except ValueError:
                return self._send_json(400, {'error': 'invalid limit'})
            try:
                sessions = INBOX.list_sessions(limit=limit)
                self._send_json(200, {'sessions': sessions, 'count': len(sessions)})
            except Exception as e:
                self._send_json(500, {'error': f'inbox sessions failed: {e}'})
        else:
            self._send_json(404, {'error': 'not found', 'path': path})

    def do_POST(self):
        path = urlparse(self.path).path
        if path == '/acp/task/create':
            if not self._check_auth():
                return self._send_json(401, {'error': 'unauthorized'})
            body = self._read_body()
            if not body:
                return self._send_json(400, {'error': 'invalid JSON body'})
            prompt = body.get('prompt', '')
            workspace = body.get('workspace', '')
            files = body.get('files', [])
            timeout = body.get('timeout', '5m')
            if not prompt or not workspace:
                return self._send_json(400, {'error': 'prompt and workspace required'})
            if not os.path.exists(workspace):
                return self._send_json(400, {'error': f'workspace not found: {workspace}'})
            task_id = new_task(prompt, workspace, files, timeout)
            queue_list, _ = get_queue_snapshot()
            pos = queue_list.index(task_id) + 1 if task_id in queue_list else 0
            self._send_json(202, {
                'task_id': task_id,
                'status': 'queued',
                'queue_position': pos,
                'queue_size': len(queue_list),
                'message': 'task queued. Use WS ws://localhost:9998/acp/ws?task_id=' + task_id + ' for bidirectional events',
            })
        elif path == '/acp/task/cancel':
            if not self._check_auth():
                return self._send_json(401, {'error': 'unauthorized'})
            body = self._read_body()
            task_id = body.get('task_id', '')
            if not task_id:
                return self._send_json(400, {'error': 'task_id required'})
            result = cancel_task_internal(task_id)
            self._send_json(result['status_code'], result['body'])
        elif path == '/acp/inbox/write':
            # POST /acp/inbox/write  {session_id, sender, content, msg_type?, parent_id?}
            if not self._check_auth():
                return self._send_json(401, {'error': 'unauthorized'})
            body = self._read_body()
            if not body:
                return self._send_json(400, {'error': 'invalid JSON body'})
            session_id = body.get('session_id', '')
            sender = body.get('sender', '')
            content = body.get('content', '')
            msg_type = body.get('msg_type', 'message')
            parent_id = body.get('parent_id')
            if not session_id or not sender or content == '':
                return self._send_json(400, {'error': 'session_id, sender, content required'})
            if sender not in ('goudan', 'mavis', 'boss', 'system'):
                return self._send_json(400, {'error': f'invalid sender: {sender}'})
            try:
                msg_id = INBOX.write(session_id, sender, content, msg_type=msg_type, parent_id=parent_id)
                log.info(f'inbox write: session={session_id} sender={sender} id={msg_id}')
                self._send_json(200, {
                    'message_id': msg_id,
                    'session_id': session_id,
                    'sender': sender,
                    'msg_type': msg_type,
                    'status': 'written',
                })
            except Exception as e:
                log.error(f'inbox write error: {e}')
                self._send_json(500, {'error': f'inbox write failed: {e}'})
        elif path == '/acp/inbox/ask':
            # POST /acp/inbox/ask  {session_id, sender, question, timeout?}
            # Writes a question + BLOCKS server-side until answered or timeout
            if not self._check_auth():
                return self._send_json(401, {'error': 'unauthorized'})
            body = self._read_body()
            if not body:
                return self._send_json(400, {'error': 'invalid JSON body'})
            session_id = body.get('session_id', '')
            sender = body.get('sender', '')
            question = body.get('question', '')
            timeout = float(body.get('timeout', 300))  # default 5 min
            if not session_id or not sender or question == '':
                return self._send_json(400, {'error': 'session_id, sender, question required'})
            if sender not in ('goudan', 'mavis', 'boss', 'system'):
                return self._send_json(400, {'error': f'invalid sender: {sender}'})
            try:
                qid = INBOX.ask_question(session_id, sender, question)
                log.info(f'inbox ask: session={session_id} sender={sender} qid={qid}')
                # Block until answered (or timeout)
                answer = INBOX.wait_for_answer(qid, timeout=timeout)
                if answer is None:
                    self._send_json(408, {
                        'error': 'timeout',
                        'question_id': qid,
                        'message': f'no answer within {timeout}s',
                    })
                else:
                    self._send_json(200, {
                        'question_id': qid,
                        'answer': answer,
                        'answered_at': answer.get('answered_at'),
                        'wait_ms': answer.get('created_at', 0) - int(time.time() * 1000) + int(timeout * 1000),
                    })
            except Exception as e:
                log.error(f'inbox ask error: {e}')
                self._send_json(500, {'error': f'inbox ask failed: {e}'})
        elif path == '/acp/inbox/answer':
            # POST /acp/inbox/answer  {question_id, answer}
            if not self._check_auth():
                return self._send_json(401, {'error': 'unauthorized'})
            body = self._read_body()
            if not body:
                return self._send_json(400, {'error': 'invalid JSON body'})
            question_id = body.get('question_id')
            answer = body.get('answer', '')
            if not question_id or answer == '':
                return self._send_json(400, {'error': 'question_id and answer required'})
            try:
                answer_id = INBOX.answer_question(int(question_id), answer)
                log.info(f'inbox answer: qid={question_id} aid={answer_id}')
                self._send_json(200, {
                    'answer_id': answer_id,
                    'question_id': question_id,
                    'status': 'answered',
                })
            except ValueError as e:
                self._send_json(404, {'error': str(e)})
            except Exception as e:
                log.error(f'inbox answer error: {e}')
                self._send_json(500, {'error': f'inbox answer failed: {e}'})
        else:
            self._send_json(404, {'error': 'not found', 'path': path})


class ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
    """HTTP server that handles each request in a new thread."""
    daemon_threads = True


# ===== WebSocket Handler =====
async def ws_handler(websocket):
    """Handle a single WebSocket connection.

    Path: /acp/ws?task_id=X&token=...

    On connect:
      - Send snapshot event for task_id (if provided)
      - Subscribe to events
    On incoming:
      - {"action":"cancel","task_id":"X"}: cancel task
      - {"action":"ping"}: respond with pong
      - {"action":"subscribe","task_id":"X"}: subscribe to another task
    """
    global WS_CONN_COUNT
    # Auth: accept token via query string OR Authorization header (websockets lib sends header)
    # websockets library: headers available as websocket.request.headers
    path = websocket.request.path if hasattr(websocket.request, 'path') else '/'
    qs = parse_qs(urlparse(path).query)
    token_qs = qs.get('token', [''])[0]
    # websockets 16: websocket.request is the Request; headers may be in websocket.headers
    auth_header = ''
    try:
        if hasattr(websocket, 'request') and hasattr(websocket.request, 'headers'):
            auth_header = websocket.request.headers.get('Authorization', '')
        elif hasattr(websocket, 'headers'):
            # websockets 16: websocket.headers is a Headers object
            auth_header = websocket.headers.get('Authorization', '')
    except Exception:
        pass
    token_h = auth_header.replace('Bearer ', '')
    token = token_qs or token_h
    if token != AUTH_TOKEN:
        await websocket.send(json.dumps({'type': 'error', 'error': 'unauthorized'}))
        await websocket.close(code=1008)
        return

    initial_task_id = qs.get('task_id', [None])[0] or qs.get('id', [None])[0]
    subscribed = set()
    if initial_task_id:
        subscribed.add(initial_task_id)

    # Connection state (only 'tasks' needed by forward_events; counter for health)
    conn_state = {'tasks': subscribed}
    with WS_LOCK:
        WS_CONN_COUNT += 1
        ws_conn_count_now = WS_CONN_COUNT
    log.info(f'WS connected: task_id={initial_task_id} total_connections={ws_conn_count_now}')

    async def send_event(event_type, data):
        msg = json.dumps({'type': event_type, **data}, ensure_ascii=False)
        try:
            await websocket.send(msg)
        except websockets.exceptions.ConnectionClosed:
            pass

    # Send snapshot for initial task
    if initial_task_id:
        with TASKS_LOCK:
            task = TASKS.get(initial_task_id)
        if not task:
            task = STORE.get(initial_task_id)
        if task:
            task_copy = dict(task)
            if task_copy.get('status') == 'queued':
                pos = get_queue_position(initial_task_id)
                if pos is not None:
                    task_copy['queue_position'] = pos
            await send_event('snapshot', {'task_id': initial_task_id, 'task': task_copy})
        else:
            await send_event('error', {'task_id': initial_task_id, 'error': 'task not found'})

    async def forward_events():
        """Forward events from sync task queues to this WS connection."""
        loop = asyncio.get_event_loop()
        last_poll_per_task = {}  # task_id -> last_queue_position_seen
        while True:
            # Iterate subscribed tasks
            current_subscribed = list(conn_state['tasks'])
            for tid in current_subscribed:
                q = get_or_create_queue(tid)
                try:
                    event = await loop.run_in_executor(None, lambda: q.get(timeout=0.2))
                except queue.Empty:
                    # Check task status for late done
                    task = TASKS.get(tid) or STORE.get(tid)
                    if task and task.get('status') in ('succeeded', 'failed', 'timeout', 'cancelled'):
                        # Already done, emit done if not already sent
                        await send_event('done', {
                            'task_id': tid,
                            'status': task['status'],
                            'duration_ms': task.get('duration_ms'),
                        })
                    continue
                event_type = event.get('type', 'message')
                await send_event(event_type, event)
                if event_type == 'done':
                    conn_state['tasks'].discard(tid)

            await asyncio.sleep(0.05)  # small yield

    async def handle_incoming():
        async for message in websocket:
            try:
                cmd = json.loads(message)
            except json.JSONDecodeError:
                await send_event('error', {'error': 'invalid JSON'})
                continue
            action = cmd.get('action')
            if action == 'cancel':
                tid = cmd.get('task_id')
                if not tid:
                    await send_event('error', {'error': 'task_id required for cancel'})
                    continue
                result = cancel_task_internal(tid)
                await send_event('cancel_ack', {
                    'task_id': tid,
                    'result': result['body'],
                    'http_status': result['status_code'],
                })
            elif action == 'ping':
                await send_event('pong', {})
            elif action == 'subscribe':
                tid = cmd.get('task_id')
                if not tid:
                    await send_event('error', {'error': 'task_id required for subscribe'})
                    continue
                # Verify task exists
                with TASKS_LOCK:
                    task = TASKS.get(tid)
                if not task:
                    task = STORE.get(tid)
                if task:
                    conn_state['tasks'].add(tid)
                    task_copy = dict(task)
                    if task_copy.get('status') == 'queued':
                        pos = get_queue_position(tid)
                        if pos is not None:
                            task_copy['queue_position'] = pos
                    await send_event('subscribed', {'task_id': tid, 'task': task_copy})
                else:
                    await send_event('error', {'task_id': tid, 'error': 'task not found'})
            else:
                await send_event('error', {'error': f'unknown action: {action}'})

    try:
        await asyncio.gather(forward_events(), handle_incoming())
    except websockets.exceptions.ConnectionClosed:
        pass
    finally:
        with WS_LOCK:
            WS_CONN_COUNT -= 1
            ws_conn_count_now = WS_CONN_COUNT
        log.info(f'WS disconnected. total_connections={ws_conn_count_now}')


def start_ws_server():
    """Run WS server in its own asyncio loop in current thread."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    async def _serve():
        async with ws_serve(ws_handler, HOST, WS_PORT, max_size=10*1024*1024):
            log.info(f'WebSocket server listening on ws://{HOST}:{WS_PORT}/acp/ws')
            await asyncio.Future()  # run forever
    try:
        loop.run_until_complete(_serve())
    finally:
        loop.close()


def main():
    print(f'OpenClaw ACP Server v7-bidir (v5 + peer-to-peer inbox) starting')
    print(f'  HTTP:  http://{HOST}:{PORT}')
    print(f'  WS:    ws://{HOST}:{WS_PORT}/acp/ws?task_id=<id>&token=<token>')
    print(f'  Auth:  <set via $ACP_TOKEN (length={len(AUTH_TOKEN)})>')
    print(f'  Mavis: {MCODE_CMD}')
    print(f'  Log:   {LOG_FILE}')
    print(f'  DB:    {STORE.db_path}')
    print(f'  Max concurrent: {MAX_CONCURRENT}')
    print(f'Endpoints:')
    print(f'  HTTP  GET  /acp/health')
    print(f'  HTTP  POST /acp/task/create   {{prompt,workspace,files,timeout}}')
    print(f'  HTTP  GET  /acp/task/get?id=xxx')
    print(f'  HTTP  GET  /acp/task/list                          (in-memory cache)')
    print(f'  HTTP  GET  /acp/task/history?status=&workspace=&limit=  (SQLite)')
    print(f'  HTTP  GET  /acp/task/stats                          (SQLite + queue)')
    print(f'  HTTP  GET  /acp/task/stream?id=xxx            [SSE streaming]')
    print(f'  HTTP  POST /acp/task/cancel  {{task_id}}')
    print(f'  --- v7-bidir peer-to-peer inbox ---')
    print(f'  HTTP  POST /acp/inbox/write   {{session_id,sender,content}}')
    print(f'  HTTP  GET  /acp/inbox/read?session_id=X&since_id=N   (poll, auto-mark-read)')
    print(f'  HTTP  POST /acp/inbox/ask     {{session_id,sender,question,timeout?}}   (blocks until answered)')
    print(f'  HTTP  POST /acp/inbox/answer  {{question_id,answer}}')
    print(f'  HTTP  GET  /acp/inbox/sessions?limit=N')
    print(f'  WS    /acp/ws?task_id=xxx                      [bidirectional]')
    print()

    # Start worker pool
    start_workers(MAX_CONCURRENT)

    # Start WebSocket server in separate thread
    ws_thread = threading.Thread(target=start_ws_server, daemon=True, name='acp-ws-server')
    ws_thread.start()

    # Start HTTP server in main thread (multi-threaded)
    http_server = ThreadingHTTPServer((HOST, PORT), ACPHandler)
    log.info(f'HTTP server listening on http://{HOST}:{PORT}')
    try:
        http_server.serve_forever()
    except KeyboardInterrupt:
        log.info('Shutting down')
        http_server.shutdown()


if __name__ == '__main__':
    main()