"""D. WebSocket bidirectional end-to-end test

Tests:
1. WS connect to ws://localhost:9998/acp/ws?task_id=X&token=...
2. Receive snapshot event
3. Create a task via HTTP (which triggers start, output, done via WS)
4. WS receives start + output + done events
5. Test bidirectional: send {"action":"cancel","task_id":"X"} via WS
   on a long-running task → verify task is cancelled
6. Test ping/pong
"""
import sys
import re
import time
import json
import asyncio
import urllib.request

import websockets

# Read token from server.py via regex (avoid redaction self-corruption)
SERVER_PY = r'%USERPROFILE%\.openclaw\skills\mavis-coding\acp-server.py'
with open(SERVER_PY, 'r', encoding='utf-8') as f:
    content = f.read()
m = re.search(r"ACP_TOKEN',\s*'([^']+)'", content)
TOKEN = m.group(1) if m else None
if not TOKEN:
    print('[FAIL] could not read token')
    sys.exit(1)

HTTP_BASE = 'http://127.0.0.1:9999'
WS_BASE = 'ws://127.0.0.1:9998'
WORKSPACE = r'%USERPROFILE%\.openclaw\workspace'


def http_get(path):
    req = urllib.request.Request(f'{HTTP_BASE}{path}', headers={'Authorization': f'Bearer {TOKEN}'})
    return json.loads(urllib.request.urlopen(req, timeout=10).read())


def http_post(path, body):
    req = urllib.request.Request(
        f'{HTTP_BASE}{path}',
        data=json.dumps(body, ensure_ascii=False).encode('utf-8'),
        headers={'Authorization': f'Bearer {TOKEN}', 'Content-Type': 'application/json'},
        method='POST',
    )
    return json.loads(urllib.request.urlopen(req, timeout=10).read())


async def collect_ws_events(ws, timeout=30.0):
    """Collect all events from WS until 'done' or timeout."""
    events = []
    try:
        async with asyncio.timeout(timeout):
            async for msg in ws:
                event = json.loads(msg)
                events.append(event)
                if event.get('type') == 'done':
                    break
    except (asyncio.TimeoutError, TimeoutError):
        pass
    except websockets.exceptions.ConnectionClosed:
        pass
    return events


print('=== D. WebSocket Bidirectional Test ===\n')

# Step 1: Verify v5-ws + WS port
print('[1] Health check (verify v5-ws)')
health = http_get('/acp/health')
print(f'  version: {health.get("version")}')
ws_info = health.get('ws', {})
print(f'  WS port: {ws_info.get("port")}')
assert health.get('version') == 'v5-ws', f'expected v5-ws'
assert ws_info.get('port') == 9998, f'expected WS port 9998'
print('  [OK] v5-ws + WS 9998 confirmed\n')

# Step 2: Create a quick task + open WS, verify events flow
print('[2] Create quick task + WS subscribe')
create_resp = http_post('/acp/task/create', {
    'prompt': 'D测试 - 用一句话回答',
    'workspace': WORKSPACE,
    'timeout': '2m',
})
task_id = create_resp['task_id']
print(f'  Created task: {task_id}')

ws_url = f'{WS_BASE}/acp/ws?task_id={task_id}&token={TOKEN}'
print(f'  WS connect: {ws_url[:60]}...')

async def test_quick_task():
    async with websockets.connect(ws_url, open_timeout=5) as ws:
        events = await collect_ws_events(ws, timeout=20.0)
        return events

events = asyncio.run(test_quick_task())
print(f'  Received {len(events)} events:')
for e in events:
    t = e.get('type', '?')
    print(f'    [{t}] task_id={e.get("task_id", "?")[:20]}... status={e.get("status", "")} ' +
          (f'answer={e.get("answer_preview", "")[:60]}' if e.get('answer_preview') else ''))

types = set(e.get('type') for e in events)
required = {'snapshot', 'start', 'done'}
missing = required - types
assert not missing, f'missing events: {missing}'
print('  [OK] snapshot + start + done all received\n')

# Step 3: Test ping/pong
print('[3] Test ping/pong (bidirectional)')
async def test_ping():
    async with websockets.connect(f'{WS_BASE}/acp/ws?token={TOKEN}', open_timeout=5) as ws:
        await ws.send(json.dumps({'action': 'ping'}))
        msg = await asyncio.wait_for(ws.recv(), timeout=5)
        return json.loads(msg)

pong = asyncio.run(test_ping())
print(f'  Sent: ping')
print(f'  Received: {pong}')
assert pong.get('type') == 'pong', f'expected pong, got {pong}'
print('  [OK] ping/pong works\n')

# Step 4: Test bidirectional cancel via WS
print('[4] Test bidirectional cancel (long-running task)')
slow_resp = http_post('/acp/task/create', {
    'prompt': 'D cancel test - 做一个长任务：列5个理由说明 AI agent 重要',
    'workspace': WORKSPACE,
    'timeout': '5m',
})
slow_task_id = slow_resp['task_id']
print(f'  Created slow task: {slow_task_id}')

async def test_cancel():
    async with websockets.connect(
        f'{WS_BASE}/acp/ws?task_id={slow_task_id}&token={TOKEN}',
        open_timeout=5
    ) as ws:
        events = []
        # Snapshot
        snapshot = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
        events.append(snapshot)
        print(f'    snapshot received: status={snapshot["task"]["status"]}')

        # Wait for start event
        while True:
            msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
            events.append(msg)
            if msg.get('type') == 'start':
                print(f'    start received (task now running)')
                break

        # Send cancel command via WS (bidirectional!)
        print(f'    >> sending cancel via WS: {slow_task_id[:24]}...')
        await ws.send(json.dumps({'action': 'cancel', 'task_id': slow_task_id}))

        # Collect ALL remaining events (including cancel_ack and done)
        try:
            while True:
                msg = await asyncio.wait_for(ws.recv(), timeout=10)
                e = json.loads(msg)
                events.append(e)
                if e.get('type') == 'done':
                    break
        except (asyncio.TimeoutError, TimeoutError):
            pass
        return events

cancelled_events = asyncio.run(test_cancel())
print(f'  Received {len(cancelled_events)} events after cancel:')
for e in cancelled_events:
    print(f'    [{e.get("type")}] status={e.get("status", "")}')

assert any(e.get('type') == 'cancel_ack' for e in cancelled_events), 'no cancel_ack'
assert any(e.get('type') == 'done' and e.get('status') == 'cancelled' for e in cancelled_events), \
    f'task not actually cancelled via WS! events: {cancelled_events}'
print('  [OK] WS cancel worked bidirectionally\n')

# Step 5: Verify final state via HTTP
print('[5] Verify cancelled task via HTTP')
final = http_get(f'/acp/task/get?id={slow_task_id}')
print(f'  status: {final.get("status")}')
print(f'  error: {final.get("error")}')
assert final.get('status') == 'cancelled', f'expected cancelled, got {final.get("status")}'
print('  [OK] Cancelled task persists in DB\n')

print('=== D. WebSocket Bidirectional Test: PASSED ===')
print('Bidirectional control channel via WS working:')
print('  - Subscribe to events: ✓')
print('  - Receive real-time events: ✓')
print('  - Send commands (cancel): ✓')
print('  - Receive cancel_ack + done: ✓')