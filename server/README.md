# OpenClaw ACP Server — Mavis Coding ACP Wrapper (v5-ws)

## What

HTTP server (port 9999) + WebSocket server (port 9998) exposing **Mavis Coding CLI** via **ACP (Agent Communication Protocol)**.
v5 adds bidirectional WebSocket control channel. See `memory/topics/acp-system.md` for full history.

```
[Agent / IDE / Another OpenClaw session]
    ↓ HTTP (acp_client.py / acp_cli.py / curl)
    ↓ WS (ws://localhost:9998/acp/ws?task_id=X&token=***)
[OpenClaw ACP Server :9999 + :9998]   ← this server (v5-ws)
    ↓ cmd.exe /c mcode.cmd (UTF-8)
[Mavis Coding CLI]            ← Mavis stays untouched
```

## Files

- `acp-server.py` — HTTP server (~19K bytes, stdlib only, no extra deps). v2 has SSE.
- `acp_client.py` — Python SDK for ACP. Use this in your own agents.
- `demo_sse.py` — One-click demo: real-time progress visualization.
- `acp-server.py.v1.bak` — v1 backup (kept for rollback)
- `scripts/run.ps1` — Original Mavis Coding wrapper (we bypass this; PS 5.1 buggy)

## Run

```powershell
python %USERPROFILE%\.openclaw\skills\mavis-coding\acp-server.py
```

Server listens on `127.0.0.1:9999` by default. v2 prints "v2-sse" in `/acp/health`.

## Endpoints

| Method | Path | Auth | Purpose |
|---|---|---|---|
| GET | `/acp/health` | no | Health check (includes `version` + `active_queues`) |
| POST | `/acp/task/create` | yes | Create+start a task |
| GET | `/acp/task/get?id=xxx` | yes | Poll task status (final answer) |
| GET | `/acp/task/list` | yes | List recent tasks (max 50) |
| **GET** | **`/acp/task/stream?id=xxx`** | **yes** | **SSE stream (NEW in v2): real-time events** |
| POST | `/acp/task/cancel` | yes | Cancel running task (notifies SSE subscribers) |

## SSE Events (v2)

The `GET /acp/task/stream` endpoint emits these events:

| Event | When | Payload |
|---|---|---|
| `snapshot` | Once on connect | Full current task state |
| `start` | When task starts | `{task_id, prompt_preview}` |
| `output` | Each stdout/stderr line from Mavis | `{stream: 'stdout'|'stderr', line}` |
| `done` | When task finishes | `{status, duration_ms, answer_preview?, error?}` |

Keepalive (`: keepalive\n\n`) is sent every 15s if no events. Queue kept alive 5 min after `done` for late reconnects.

## Auth

Bearer token. Default: `openclaw-acp-demo-token` (extracted from `acp-server.py` source by `acp_client.py`).
Override via env: `ACP_TOKEN=your-token`

## Quickstart with Python SDK

```python
from acp_client import ACPClient

client = ACPClient()  # auto-reads token from server.py

# Option A: create + poll (simple)
task_id = client.create_task("say hi in 3 words", workspace="C:\\Users\\Administrator\\.openclaw\\workspace")
result = client.wait_for_task(task_id, timeout=120)
print(result['answer'])

# Option B: create + stream (real-time)
def on_event(event_type, data):
    if event_type == 'output':
        print(f"[{data['stream']}] {data['line']}")
    elif event_type == 'done':
        print(f"DONE: {data['status']} in {data['duration_ms']}ms")

result = client.run_and_stream(
    "say hi in 3 words",
    workspace="C:\\Users\\Administrator\\.openclaw\\workspace",
    on_event=on_event,
)
print(f"Answer: {result['answer']}")
```

## One-Click Demo

```powershell
python %USERPROFILE%\.openclaw\skills\mavis-coding\demo_sse.py
```

Runs a real Mavis task and prints every line + final answer in real-time. Best way to feel SSE.

## Raw curl Examples

```bash
# Create task
curl -X POST http://127.0.0.1:9999/acp/task/create \
  -H "Authorization: Bearer openclaw-acp-demo-token" \
  -H "Content-Type: application/json" \
  -d '{"prompt":"say hello","workspace":"C:\\path","timeout":"2m"}'

# SSE stream (curl auto-parses)
curl -N http://127.0.0.1:9999/acp/task/stream?id=task_xxx \
  -H "Authorization: Bearer openclaw-acp-demo-token"
```

## Why we built this

1. **OpenClaw is the Agent platform** — owns the protocol layer
2. **Mavis Coding is a CLI** — shouldn't carry protocol weight
3. Boss 7/25 Codex system split: Mavis via ACP, Codex via MCP — separation is good
4. Avoids PowerShell 5.1 quoting hell (no `&` `$()` `Format-Table` issues)
5. v2 SSE makes long Mavis tasks observable in real time

## v5 vs v1 changes

- ✅ **NEW (v2):** SSE streaming via `GET /acp/task/stream`
- ✅ **NEW (v3):** SQLite persistence + `/acp/task/history` + `/acp/task/stats`
- ✅ **NEW (v4):** Worker pool + FIFO queue + `queued` status (MAX_CONCURRENT=3)
- ✅ **NEW (v5):** WebSocket bidirectional on port 9998 (cancel/ping/subscribe)
- ✅ Server reads Mavis stdout/stderr as UTF-8 (was system default GBK → garbled Chinese)
- ✅ Cancel endpoint notifies both SSE + WS subscribers via queue
- ✅ Health endpoint reports `version` + `active_queues` + `queue.size/active/max` + `ws.active_connections`
- ✅ `nuke-restart.py` for clean restart (handles zombie processes)

## Known issues / Roadmap

- ✅ ~~Persistent task storage (SQLite)~~ — done v3
- ✅ ~~Concurrency limit / queue~~ — done v4
- ✅ ~~WebSocket bidirectional~~ — done v5
- ✅ ~~Integration into OpenClaw platform~~ — done F (see `~/.openclaw/workspace/skills/acp-integration/`)
- [ ] Multi-agent routing (M2.5 / M3 / Codex by task type) — **E 待开发**
- [ ] Token auth is minimal (demo only — real prod needs OAuth)
- [ ] Watchdog (auto-restart on crash)
- [ ] ComfyUI integration via ACP