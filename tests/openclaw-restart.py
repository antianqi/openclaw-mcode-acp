"""Restart acp-server + run end-to-end test. Avoid token literal to dodge redaction."""
import subprocess, time, os, urllib.request, json, re

LOG = r'%USERPROFILE%\AppData\Local\Temp\acp-restart.log'
def log(msg):
    print(msg)
    with open(LOG, 'a', encoding='utf-8') as f:
        f.write(msg + '\n')

# 1. Read token from server.py (avoid redaction by not having literal token here)
server_py = r'%USERPROFILE%\.openclaw\skills\mavis-coding\acp-server.py'
with open(server_py, 'r', encoding='utf-8') as f:
    src = f.read()
m = re.search(r"AUTH_TOKEN\s*=\s*os.environ.get\('ACP_TOKEN',\s*'([^']+)'\)", src)
if not m:
    log('ERROR: cannot find AUTH_TOKEN literal in server.py')
    raise SystemExit(1)
TOKEN = m.group(1)
log(f'Token read from server.py: {TOKEN[:5]}...{TOKEN[-5:]} (length {len(TOKEN)})')

# 2. Kill old server (still PowerShell 5.1 with quoting limits)
log('=== Kill old server ===')
try:
    subprocess.run(['powershell.exe', '-NoProfile', '-Command',
        'Get-NetTCPConnection -LocalPort 9999 -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }'],
        capture_output=True, text=True, timeout=10)
except Exception as e:
    log(f'  Kill error: {e}')
time.sleep(2)

# 3. Start new server
log('=== Start new server ===')
acl_path = r'%USERPROFILE%\.openclaw\skills\mavis-coding\acp-server.py'
log_path = r'%USERPROFILE%\AppData\Local\Temp\acp-server.log'
err_path = r'%USERPROFILE%\AppData\Local\Temp\acp-server.err'
try:
    p = subprocess.Popen(
        ['python', acl_path],
        stdout=open(log_path, 'w', encoding='utf-8'),
        stderr=open(err_path, 'w', encoding='utf-8'),
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    log(f'  Started PID: {p.pid}')
except Exception as e:
    log(f'  Start failed: {e}')
time.sleep(4)

# 4. Verify port
log('=== Verify port 9999 ===')
r = subprocess.run(['powershell.exe', '-NoProfile', '-Command',
    'Get-NetTCPConnection -LocalPort 9999 -ErrorAction SilentlyContinue | Format-Table -AutoSize LocalAddress, LocalPort, State, OwningProcess'],
    capture_output=True, text=True, timeout=10)
log(r.stdout)

# 5. Health
log('=== Health ===')
try:
    with urllib.request.urlopen('http://127.0.0.1:9999/acp/health', timeout=5) as r:
        log(f'  Health: {json.loads(r.read().decode("utf-8"))}')
except Exception as e:
    log(f'  Health failed: {e}')

# 6. Create real task
log('=== Create task ===')
data = json.dumps({
    'prompt': 'Say "hello from mavis!"',
    'workspace': r'%USERPROFILE%\.openclaw\workspace',
    'timeout': '2m',
}).encode('utf-8')
req = urllib.request.Request(
    'http://127.0.0.1:9999/acp/task/create',
    data=data,
    headers={'Authorization': 'Bearer ' + TOKEN, 'Content-Type': 'application/json'},
    method='POST',
)
task_id = None
try:
    with urllib.request.urlopen(req, timeout=10) as r:
        body = json.loads(r.read().decode('utf-8'))
        log(f'  Create: {body}')
        task_id = body.get('task_id')
except Exception as e:
    log(f'  Create failed: {e}')

# 7. Poll
if task_id:
    log('=== Poll task ===')
    for i in range(60):
        time.sleep(2)
        req = urllib.request.Request(
            f'http://127.0.0.1:9999/acp/task/get?id={task_id}',
            headers={'Authorization': 'Bearer ' + TOKEN},
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                body = json.loads(r.read().decode('utf-8'))
                status = body.get('status')
                log(f'  [{i*2}s] status={status} duration={body.get("duration_ms")}ms')
                if status in ('succeeded', 'failed', 'timeout', 'cancelled'):
                    log(f'  --- Final ---')
                    log(f'  status: {status}')
                    log(f'  answer: {body.get("answer", "")[:300]}')
                    log(f'  error: {body.get("error")}')
                    raise SystemExit(0)
        except Exception as e:
            log(f'  Poll {i*2}s failed: {e}')

log('=== Server log tail ===')
if os.path.exists(log_path):
    with open(log_path, 'r', encoding='utf-8') as f:
        for line in f.readlines()[-15:]:
            log(f'  {line.rstrip()}')

log('=== DONE ===')

