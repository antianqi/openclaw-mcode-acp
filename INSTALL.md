# INSTALL — OpenClaw ACP Project

## Prerequisites

- **Python 3.10+** (tested on 3.14)
- **Windows** (cmd.exe via mcode.cmd; on Mac/Linux replace `cmd.exe /c` with `bash -c`)
- **Mavis Coding CLI** installed and accessible at `~/.minimax-code/mcode.cmd` (adjust path if different)
- **OpenClaw** (if using the F integration)

## Setup

### 1. Install Python dependencies

```powershell
cd D:\openclaw-acp
pip install -r requirements.txt
```

`requirements.txt` contains:
```
websockets>=16.0
```

That's it. Everything else is Python stdlib.

### 2. Configure (optional)

Set environment variables if defaults don't fit:

```powershell
# Optional overrides
$env:ACP_PORT = 9999
$env:ACP_WS_PORT = 9998
$env:ACP_MAX_CONCURRENT = 3
$env:ACP_TOKEN = "openclaw-acp-demo-token"
```

For production: change `ACP_TOKEN` to a strong secret, set `ACP_HOST=0.0.0.0` if external clients need to connect.

### 3. Start the server

```powershell
.\scripts\start_server.bat
```

Or directly:
```powershell
python server\acp-server.py
```

The server prints:
```
OpenClaw ACP Server v5 (SSE + SQLite + Queue + WS) starting
  HTTP:  http://127.0.0.1:9999
  WS:    ws://127.0.0.1:9998/acp/ws?task_id=<id>&token=<token>
  ...
```

Server runs in foreground. For background, use the bat script or:
```powershell
Start-Process python server\acp-server.py -WindowStyle Hidden
```

### 4. Verify

```powershell
python openclaw-skill\acp_cli.py health
```

Expected output:
```json
{
  "status": "ok",
  "version": "v5-ws",
  "ws": {"port": 9998, "active_connections": 0},
  "db": {"total_tasks": 0, "by_status": {}},
  ...
}
```

### 5. Run a real test

```powershell
python openclaw-skill\acp_cli.py create --prompt "用一句话回答" --workspace "%USERPROFILE%\.openclaw\workspace"
# → task_xxxxxxxxxxxxxxxxxxxxxxxx

python openclaw-skill\acp_cli.py wait --id task_xxxxxxxxxxxxxxxxxxxxxxxx --timeout 60
# → {"status": "succeeded", "answer": "...", ...}
```

### 6. Stop the server

```powershell
.\scripts\stop_server.bat
```

Or:
```powershell
Get-Process python | Where-Object { $_.CommandLine -like '*acp-server*' } | Stop-Process
```

## OpenClaw integration (optional)

If you want ACP usable as a native OpenClaw skill:

```powershell
# Symlink (or copy) the openclaw-skill directory into OpenClaw's workspace skills:
New-Item -ItemType Junction -Path "$env:USERPROFILE\.openclaw\workspace\skills\acp-integration" -Target "D:\openclaw-acp\openclaw-skill"

# Verify
python "$env:USERPROFILE\.openclaw\workspace\skills\acp-integration\acp_cli.py" health
```

From any OpenClaw session, import directly:
```python
import sys
sys.path.insert(0, r'D:\openclaw-acp\openclaw-skill')
from acp_tools import create_task, wait_task
task_id = create_task("...", workspace="...")
result = wait_task(task_id)
```

## Troubleshooting

**Port already in use:**
```
Get-NetTCPConnection -LocalPort 9999 -ErrorAction SilentlyContinue
# Find the PID, then:
Get-Process -Id <PID>
# If it's an old acp-server.py, kill it. Use tests/nuke-restart.py for cleanup.
```

**`AUTH_TOKEN` redaction in source:**
See `PITFALLS.md` #1. Use `tests/fix-server-auth.py` or manually replace.

**WebSocket handler error `'WebSocketServerProtocol' object has no attribute 'request'`:**
Wrong websockets API. Use `websockets.asyncio.server`, not legacy `websockets.server`. See PITFALLS #6.

**Task stuck in queued:**
Workers (3 by default) all busy. Either wait for queue to drain, increase `ACP_MAX_CONCURRENT`, or cancel some.

**Task fails with `'NoneType' object has no attribute 'strip'`:**
Server was killed mid-subprocess. Use `nuke-restart.py` to clean zombies.

## Next steps

- Read [PITFALLS.md](PITFALLS.md) before modifying server code
- Read [docs/acp-system-notes.md](docs/acp-system-notes.md) for full architecture
- See `tests/` for example test patterns