"""SSE end-to-end test: subscribe to a running task and read events as they come"""
import urllib.request
import urllib.error
import json
import time
import sys
import io
import os

# Force stdout to UTF-8 to avoid GBK encode errors on Windows
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

TOKEN = 'openclaw-acp-demo-token'
BASE = 'http://127.0.0.1:9999'

# Read token from server.py source to avoid write-tool redaction
with open(r'%USERPROFILE%\.openclaw\skills\mavis-coding\acp-server.py', 'r', encoding='utf-8') as f:
    content = f.read()
import re
m = re.search(r"'openclaw-acp-demo-token'", content)
if m:
    TOKEN = 'openclaw-acp-demo-token'

print(f'Token: {TOKEN[:5]}...{TOKEN[-5:]} (len={len(TOKEN)})')

# Step 1: Create a real Mavis task
print('\n=== Step 1: Create task ===')
req = urllib.request.Request(
    f'{BASE}/acp/task/create',
    data=json.dumps({
        'prompt': 'say hi in 3 words',
        'workspace': r'%USERPROFILE%\.openclaw\workspace',
        'timeout': '2m',
    }).encode('utf-8'),
    headers={
        'Content-Type': 'application/json',
        'Authorization': f'Bearer {TOKEN}',
    },
    method='POST',
)
resp = json.loads(urllib.request.urlopen(req, timeout=10).read())
task_id = resp['task_id']
print(f'Task: {task_id}')

# Step 2: Subscribe to SSE stream
print('\n=== Step 2: SSE stream (max 15s) ===')
events_received = []
start = time.time()

try:
    req = urllib.request.Request(
        f'{BASE}/acp/task/stream?id={task_id}',
        headers={'Authorization': f'Bearer {TOKEN}'},
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        print(f'SSE connected, status={resp.status}, content-type={resp.headers.get("Content-Type")}')
        # Read line by line
        buf = b''
        while time.time() - start < 18:
            chunk = resp.read(1)
            if not chunk:
                print('  [stream ended]')
                break
            buf += chunk
            if buf.endswith(b'\n\n'):
                # Parse SSE event
                lines = buf.decode('utf-8', errors='replace').rstrip('\n').split('\n')
                evt_type = None
                evt_data = None
                for line in lines:
                    if line.startswith('event: '):
                        evt_type = line[7:].strip()
                    elif line.startswith('data: '):
                        evt_data = line[6:]
                    elif line.startswith(':') and 'keepalive' in line:
                        print('  [keepalive]')
                if evt_type and evt_data:
                    try:
                        parsed = json.loads(evt_data)
                        events_received.append((evt_type, parsed))
                        if evt_type == 'snapshot':
                            print(f'  [snapshot] status={parsed["task"]["status"]}')
                        elif evt_type == 'start':
                            print(f'  [start] prompt={parsed.get("prompt","")[:40]}')
                        elif evt_type == 'output':
                            line_text = parsed.get('line','')[:80]
                            print(f'  [output/{parsed.get("stream")}] {line_text}')
                        elif evt_type == 'done':
                            print(f'  [done] status={parsed.get("status")} dur={parsed.get("duration_ms")}ms')
                            if parsed.get('answer_preview'):
                                print(f'         answer: {parsed["answer_preview"]}')
                            break
                    except json.JSONDecodeError as e:
                        print(f'  [bad JSON] {evt_data[:100]}')
                buf = b''
except urllib.error.HTTPError as e:
    print(f'  HTTP ERROR: {e.code} {e.read().decode()}')
except Exception as e:
    print(f'  ERROR: {type(e).__name__}: {e}')

elapsed = time.time() - start
print(f'\n=== Summary ===')
print(f'Time: {elapsed:.1f}s')
print(f'Events received: {len(events_received)}')
types = {}
for t, _ in events_received:
    types[t] = types.get(t, 0) + 1
print(f'Event types: {types}')

# Check we got at least: snapshot, start, output (>=1 line), done
required = ['snapshot', 'start', 'done']
missing = [t for t in required if t not in types]
if missing:
    print(f'[FAIL] MISSING: {missing}')
    sys.exit(1)
else:
    print('[OK] SSE end-to-end OK')