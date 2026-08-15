---
name: acp-integration
description: OpenClaw-native wrapper for OpenClaw ACP server (HTTP 9999 + WS 9998). Use this skill when the boss or any OpenClaw agent needs to dispatch tasks to Mavis Coding, query task history, manage the worker queue, or stream real-time events. Provides both Python module (acp_tools) and CLI (acp_cli) interfaces.
---

# ACP Integration for OpenClaw

OpenClaw's ACP (Agent Communication Protocol) server exposes Mavis Coding as a
standard HTTP + WebSocket service. This skill wraps the ACP client so OpenClaw
agents can dispatch tasks natively without shell-out or external HTTP plumbing.

## Auth model

The bearer token is **only** read from the `ACP_TOKEN` environment variable.
There is **no default value** — if `ACP_TOKEN` is not set, the server refuses
to start and the client raises `ACPTokenMissing` on construction.

- The token is sent in the `Authorization: Bearer <token>` HTTP header for
  every authenticated request.
- The same token is also accepted as the `?token=<token>` query parameter on
  the WebSocket endpoint, because browser WebSocket APIs cannot set custom
  request headers.
- The server **never** reads the token from source code, never logs the raw
  value, and never accepts a value from a prompt or stdin.

To obtain a token, run on the server host:

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

Then set it in the server's environment (`$ACP_TOKEN` or `ACP_TOKEN=...`)
**before** starting the server, and configure the same value in every
client's environment.

## Cross-platform paths

This skill uses `acp_paths.py` (in this directory) as the single source of
truth for filesystem locations. Resolution order:

| What | Env override | Default |
|---|---|---|
| Project root (`$ACP_HOME`) | `ACP_HOME` | `~/.openclaw-acp` |
| OpenClaw install (`$OPENCLAW_HOME`) | `OPENCLAW_HOME` | `~/.openclaw` |
| Mavis Coding CLI | `MCODE_CMD` | `~/.minimax-code/mcode` (POSIX) or `mcode.cmd` (Windows) |
| Session workspaces | `ACP_SESSIONS_DIR` | `<ACP_HOME>/sessions` |
| Temp / DB / log defaults | `ACP_TEMP_DIR` | stdlib `tempfile.gettempdir()` |

**There is no `D:\openclaw-acp` hardcoding.** Any drive letter works on
Windows; macOS and Linux use the `$HOME`-relative defaults.

See `docs/RUNTIME_DEPS.md` for the full version + env contract, and
`INSTALL.md` for supported platforms.

## What's inside

- **`acp_tools.py`** — Python module. Import and call directly:
  ```python
  from acp_tools import create_task, wait_task, stream_task
  task_id = create_task("do thing", workspace="/path/to/workspace")
  result = wait_task(task_id, timeout=120)
  print(result['answer'])
  ```
- **`acp_cli.py`** — Command-line wrapper. One-liners:
  ```bash
  python acp_cli.py health
  python acp_cli.py create --prompt "do thing" --workspace "/path/to/workspace"
  python acp_cli.py wait --id task_xxx --timeout 60
  python acp_cli.py stream --id task_xxx
  ```
- **`acp_client.py`** — Underlying SDK (lives at `<ACP_HOME>/client/`).
- **`acp_paths.py`** — Cross-platform path resolver (this directory).

## When to use

Use this skill when:
- Boss asks OpenClaw to run a Mavis task
- Need to dispatch work to the worker queue (concurrency limit: 3 by default)
- Want to stream real-time events from a task
- Need to query task history from SQLite
- Need to cancel a running or queued task

## When NOT to use

- Tasks that don't need Mavis (use direct Python/Node scripts)
- Long batch jobs (use multiple `create_task` calls in parallel)
- Tasks that need sub-second latency (use direct `mcode` CLI)

## Architecture

```
[OpenClaw agent / boss Feishu DM]
    ↓ acp_tools.create_task() or acp_cli.py create
[OpenClaw ACP Server :9999 (HTTP) + :9998 (WS)]
    ↓ subprocess: mcode exec --permission full
[Mavis Coding CLI]  ← external runtime dependency
```

Concurrency is throttled by the server (`MAX_CONCURRENT=3`). Excess tasks queue.

## Endpoints exposed

- HTTP `GET  /acp/health` — no auth
- HTTP `POST /acp/task/create` — enqueue a task (returns 202 + task_id)
- HTTP `GET  /acp/task/get?id=X` — poll task status
- HTTP `GET  /acp/task/list` — in-memory cache (recent 50)
- HTTP `GET  /acp/task/history?status=&workspace=` — SQLite history
- HTTP `GET  /acp/task/stats` — counts by status + queue
- HTTP `GET  /acp/task/stream?id=X` — SSE one-way streaming
- HTTP `POST /acp/task/cancel` — cancel task
- HTTP `POST /acp/inbox/write` — write peer-to-peer message (v7-bidir)
- HTTP `GET  /acp/inbox/read` — read new peer messages (auto-mark-read)
- HTTP `POST /acp/inbox/ask` — blocking question
- HTTP `POST /acp/inbox/answer` — answer pending question
- HTTP `GET  /acp/inbox/sessions` — list active sessions
- WS   `ws://localhost:9998/acp/ws?task_id=X&token=<ACP_TOKEN>` — bidirectional events

## Python API quick reference

```python
import os
os.environ['ACP_TOKEN'] = '<the token the server operator gave you>'

from acp_tools import (
    health, create_task, get_task, wait_task, list_tasks,
    cancel_task, run_and_stream, stream_task, history, stats,
)
from acp_client import ACPError, ACPTokenMissing

# Health check
h = health()
print(h['version'], h['queue']['size'], h['db']['total_tasks'])

# Create + wait (simple)
task_id = create_task("用一句话回答", workspace="/Users/me/workspace")
result = wait_task(task_id, timeout=120)
print(result['status'], result['answer'])

# Cancel
cancel_task(task_id)

# History query (SQLite)
recent = history(status='succeeded', limit=10)
for t in recent:
    print(t['id'], t['status'], t['prompt'][:50])

# Stats
s = stats()
print(s['total'], s['by_status'])
```

## Related skills

- `mavis-coding` — owns the Mavis CLI integration (the actual `mcode` runner)
- `feishu-progress` — push ACP task events to Feishu groups

## Changelog

- **v1.1** (2026-08-15): Review fixes
  - `ACP_TOKEN` is now required (no default); server refuses to start without it
  - `read_token_from_server` removed (no more source-code scraping)
  - Cross-platform path resolution via `acp_paths.py` (`ACP_HOME` env var)
  - D:\\openclaw-acp hardcoding removed from skill + SDK + peer templates
- **v1.0** (2026-08-13): F — ACP fully integrated into OpenClaw platform
