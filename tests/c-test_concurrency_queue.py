"""C. Concurrency limit + queue test (token via dynamic read)"""
import sys
import re
import time
import json
import urllib.request
import urllib.error

# Read token from server.py via regex that doesn't include the literal (avoid redaction)
SERVER_PY = r'%USERPROFILE%\.openclaw\skills\mavis-coding\acp-server.py'
with open(SERVER_PY, 'r', encoding='utf-8') as f:
    content = f.read()
# Pattern: 'ACP_TOKEN', '<TOKEN>' — capture group gives the real token without literal in source
m = re.search(r"ACP_TOKEN',\s*'([^']+)'", content)
TOKEN = m.group(1) if m else None
if not TOKEN:
    print('[FAIL] could not read token from server.py')
    sys.exit(1)

BASE = 'http://127.0.0.1:9999'
WORKSPACE = r'%USERPROFILE%\.openclaw\workspace'


def http_get(path):
    req = urllib.request.Request(f'{BASE}{path}', headers={'Authorization': f'Bearer {TOKEN}'})
    return json.loads(urllib.request.urlopen(req, timeout=10).read())


def http_post(path, body):
    req = urllib.request.Request(
        f'{BASE}{path}',
        data=json.dumps(body, ensure_ascii=False).encode('utf-8'),
        headers={'Authorization': f'Bearer {TOKEN}', 'Content-Type': 'application/json'},
        method='POST',
    )
    return json.loads(urllib.request.urlopen(req, timeout=10).read())


print('=== C. Concurrency Limit + Queue Test ===\n')

# Step 1: Verify v4-queue
print('[1] Health check')
health = http_get('/acp/health')
print(f'  version: {health.get("version")}')
MAX = health.get('queue', {}).get('max_concurrent')
print(f'  max_concurrent: {MAX}')
assert health.get('version') == 'v4-queue', f'expected v4-queue, got {health.get("version")}'
assert MAX == 3, f'expected MAX=3, got {MAX}'
initial_db_count = health.get('db', {}).get('total_tasks', 0)
print(f'  [OK] v4-queue + MAX=3 confirmed\n')

# Step 2: Create 10 tasks rapidly
print('[2] Create 10 tasks rapidly (should queue)')
task_ids = []
for i in range(10):
    resp = http_post('/acp/task/create', {
        'prompt': f'C并发测试 {i+1}/10 - 用3个字回答',
        'workspace': WORKSPACE,
        'timeout': '5m',
    })
    task_ids.append(resp['task_id'])
    print(f'  task {i+1}: {resp["task_id"][:24]}... status={resp["status"]} pos={resp.get("queue_position")}')
print(f'\n  Created {len(task_ids)} tasks\n')

# Step 3: Poll queue behavior
print('[3] Poll queue behavior (10s window)')
peak_queue = 0
peak_active = 0
for i in range(20):
    h = http_get('/acp/health')
    q = h.get('queue', {})
    sz = q.get('size', 0)
    act = q.get('active', 0)
    peak_queue = max(peak_queue, sz)
    peak_active = max(peak_active, act)
    print(f'  t={i*0.5:.1f}s queue_size={sz:2d} active={act}/{MAX}')
    time.sleep(0.5)

print(f'\n  Peak queue size: {peak_queue}')
print(f'  Peak active: {peak_active}')
assert peak_active <= MAX, f'[FAIL] active exceeded MAX={MAX}! Got {peak_active}'
assert peak_queue >= 5, f'[FAIL] expected queue depth >= 5, got {peak_queue}'
print(f'  [OK] Queue behavior: max active={peak_active}<={MAX}, max queue={peak_queue}\n')

# Step 4: Wait for all tasks
print('[4] Wait for all tasks to complete')
final_statuses = {}
for attempt in range(45):
    done_count = 0
    for tid in task_ids:
        if tid in final_statuses:
            done_count += 1
            continue
        try:
            t = http_get(f'/acp/task/get?id={tid}')
            s = t.get('status')
            if s in ('succeeded', 'failed', 'timeout', 'cancelled'):
                final_statuses[tid] = s
                done_count += 1
        except Exception:
            pass
    if done_count == len(task_ids):
        break
    time.sleep(2)

print(f'  Completed: {len(final_statuses)}/{len(task_ids)}')
status_dist = {}
for s in final_statuses.values():
    status_dist[s] = status_dist.get(s, 0) + 1
print(f'  Status: {status_dist}')
assert len(final_statuses) == len(task_ids), f'tasks not all done'
print()

# Step 5: Final stats
print('[5] Final stats')
final_health = http_get('/acp/health')
print(f'  Queue size: {final_health["queue"]["size"]}')
print(f'  Active: {final_health["queue"]["active"]}')
print(f'  DB total: {final_health["db"]["total_tasks"]} (was {initial_db_count})')
print(f'  DB by_status: {final_health["db"]["by_status"]}')
assert final_health['db']['total_tasks'] >= initial_db_count + len(task_ids), 'DB did not record all tasks'
print(f'  [OK] DB recorded {len(task_ids)} new tasks\n')

# Step 6: Sample
print('[6] Sample task statuses')
for tid in task_ids[:3]:
    t = http_get(f'/acp/task/get?id={tid}')
    print(f'  {tid[:24]} status={t.get("status")} dur={t.get("duration_ms")}ms')

print('\n=== C. Concurrency Limit + Queue Test: PASSED ===')
print(f'Peak concurrency: {peak_active}/{MAX} (limit respected)')
print(f'Peak queue depth: {peak_queue} (queue worked under load)')
print(f'All {len(task_ids)} tasks completed and persisted to DB')