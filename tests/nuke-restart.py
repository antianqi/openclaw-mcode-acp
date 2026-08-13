"""NUKE all acp-server zombies and start ONE clean v2 server"""
import os
import sys
import time
import subprocess
import urllib.request
import urllib.error
import json

# Step 1: Nuke ALL acp-server processes (by WMI)
print('=== Step 1: NUKE all acp-server python processes ===')
r = subprocess.run(
    ['powershell.exe', '-NoProfile', '-Command',
     "Get-WmiObject Win32_Process -Filter \"Name='python.exe'\" | Where-Object { $_.CommandLine -like '*acp-server*' } | ForEach-Object { Write-Host (\"killing PID \" + $_.ProcessId); Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"],
    capture_output=True, text=True, timeout=15
)
print(r.stdout)
if r.stderr: print('stderr:', r.stderr[:200])

# Also kill "python acp-server.py" invocations (no full path)
r = subprocess.run(
    ['powershell.exe', '-NoProfile', '-Command',
     "Get-WmiObject Win32_Process -Filter \"Name='python.exe'\" | Where-Object { $_.CommandLine -match 'acp-server' } | ForEach-Object { Write-Host (\"killing PID \" + $_.ProcessId); Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"],
    capture_output=True, text=True, timeout=15
)
print(r.stdout)

time.sleep(3)

# Step 2: Verify all dead
print('\n=== Step 2: Verify all dead ===')
r = subprocess.run(
    ['powershell.exe', '-NoProfile', '-Command',
     "Get-WmiObject Win32_Process -Filter \"Name='python.exe'\" | Where-Object { $_.CommandLine -match 'acp-server' } | Measure-Object | Select-Object -ExpandProperty Count"],
    capture_output=True, text=True, timeout=10
)
remaining = int(r.stdout.strip() or '0')
print(f'Remaining acp-server processes: {remaining}')

# Step 3: Verify port 9999 free
print('\n=== Step 3: Verify port 9999 free ===')
r = subprocess.run(
    ['powershell.exe', '-NoProfile', '-Command',
     '(Get-NetTCPConnection -LocalPort 9999 -ErrorAction SilentlyContinue | Measure-Object).Count'],
    capture_output=True, text=True, timeout=10
)
print(f'Port 9999 connections: {r.stdout.strip()}')

# Step 4: Start ONE clean v2 server
print('\n=== Step 4: Start clean v2 server ===')
log_path = r'%USERPROFILE%\AppData\Local\Temp\acp-server.log'
log_file = open(log_path, 'a', encoding='utf-8')
proc = subprocess.Popen(
    [r'C:\Python314\python.exe', r'%USERPROFILE%\.openclaw\skills\mavis-coding\acp-server.py'],
    stdout=log_file,
    stderr=log_file,
    cwd=r'%USERPROFILE%\.openclaw\skills\mavis-coding',
    creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
)
print(f'  Started PID: {proc.pid}')
time.sleep(4)

# Step 5: Verify port bound to new PID
print('\n=== Step 5: Verify port 9999 ===')
r = subprocess.run(
    ['powershell.exe', '-NoProfile', '-Command',
     'Get-NetTCPConnection -LocalPort 9999 -ErrorAction SilentlyContinue | Select-Object LocalAddress, LocalPort, State, OwningProcess | Format-Table -AutoSize'],
    capture_output=True, text=True, timeout=10
)
print(r.stdout)

# Step 6: Health check (ASCII only output!)
print('\n=== Step 6: Health check ===')
resp = urllib.request.urlopen('http://127.0.0.1:9999/acp/health', timeout=5)
health = json.loads(resp.read())
print(f'  Health: {health}')
if health.get('version') == 'v2-sse':
    print('  [OK] v2 server confirmed')
else:
    print(f'  [FAIL] version mismatch: expected v2-sse, got {health.get("version")}')
    sys.exit(1)

# Step 7: SSE endpoint probe
print('\n=== Step 7: SSE endpoint probe ===')
try:
    req = urllib.request.Request(
        'http://127.0.0.1:9999/acp/task/stream',
        headers={'Authorization': 'Bearer openclaw-acp-demo-token'},
    )
    resp = urllib.request.urlopen(req, timeout=3)
    print(f'  Stream status: {resp.status} (unexpected - should be 400)')
except urllib.error.HTTPError as e:
    body = e.read().decode()
    print(f'  Stream probe status: {e.code} body={body}')
    if e.code == 400 and 'id parameter required' in body:
        print('  [OK] /acp/task/stream endpoint exists')
    else:
        print(f'  [FAIL] unexpected response')
        sys.exit(1)

print('\n=== ALL CHECKS PASSED ===')
print(f'Server PID: {proc.pid}')
print('Ready to run SSE end-to-end test.')