---
name: acp-integration
description: OpenClaw-native wrapper for OpenClaw ACP server (HTTP 9999 + WS 9998). Use this skill when the boss or any OpenClaw agent needs to dispatch tasks to Mavis Coding, query task history, manage the worker queue, or stream real-time events. Provides both Python module (acp_tools) and CLI (acp_cli) interfaces.
---

# ACP Integration for OpenClaw

OpenClaw's ACP (Agent Communication Protocol) server exposes Mavis Coding as a
standard HTTP + WebSocket service. This skill wraps the ACP client so OpenClaw
agents can dispatch tasks natively without shell-out or external HTTP plumbing.

## What's inside

- **`acp_tools.py`** — Python module. Import and call directly:
  ```python
  from acp_tools import create_task, wait_task, stream_task
  task_id = create_task("do thing", workspace="C:/path")
  result = wait_task(task_id, timeout=120)
  print(result['answer'])
  ```
- **`acp_cli.py`** — Command-line wrapper. One-liners:
  ```powershell
  python acp_cli.py health
  python acp_cli.py create --prompt "do thing" --workspace "C:/path"
  python acp_cli.py wait --id task_xxx --timeout 60
  python acp_cli.py stream --id task_xxx
  ```
- **`acp_client.py`** — Underlying SDK (lives at `~/.openclaw/skills/mavis-coding/`).

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
- Tasks that need sub-second latency (use direct `mavis-coding` CLI)

## Architecture

```
[OpenClaw agent / boss Feishu DM]
    ↓ acp_tools.create_task() or acp_cli.py create
[OpenClaw ACP Server :9999 (HTTP) + :9998 (WS)]
    ↓ cmd.exe /c mcode.cmd
[Mavis Coding CLI]  ← Mavis stays untouched
```

Concurrency is throttled by the server (`MAX_CONCURRENT=3`). Excess tasks queue.

## Endpoints exposed

- HTTP `POST /acp/task/create` — enqueue a task (returns 202 + task_id)
- HTTP `GET /acp/task/get?id=X` — poll task status
- HTTP `GET /acp/task/list` — in-memory cache (recent 50)
- HTTP `GET /acp/task/history?status=&workspace=` — SQLite history
- HTTP `GET /acp/task/stats` — counts by status + queue
- HTTP `GET /acp/task/stream?id=X` — SSE one-way streaming
- HTTP `POST /acp/task/cancel` — cancel task
- HTTP `GET /acp/health` — health + queue + db stats
- WS   `ws://localhost:9998/acp/ws?task_id=X&token=Y` — bidirectional events

## Python API quick reference

```python
from acp_tools import (
    health, create_task, get_task, wait_task, list_tasks,
    cancel_task, run_and_stream, stream_task, history, stats,
)
from acp_client import ACPError

# Health check
h = health()
print(h['version'], h['queue']['size'], h['db']['total_tasks'])

# Create + wait (simple)
task_id = create_task("用一句话回答", workspace="C:/Users/.../workspace")
result = wait_task(task_id, timeout=120)
print(result['status'], result['answer'])

# Create + stream (real-time)
def on_event(evt_type, data):
    if evt_type == 'output':
        print(f"[{data['stream']}] {data['line']}")
    elif evt_type == 'done':
        print(f"DONE: {data['status']}")

result = run_and_stream(
    prompt="用一句话回答",
    workspace="C:/Users/.../workspace",
    on_event=on_event,
)
print(result['answer'])

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

## Auth

Default token: `openclaw-acp-demo-token` (auto-read from `acp-server.py` source).
Override via `ACP_TOKEN` environment variable.

## Related skills

- `mavis-coding` — owns the ACP server (this skill's backend)
- `feishu-progress` — push ACP task events to Feishu groups

## Changelog

- **v1.0** (2026-08-13): F — ACP fully integrated into OpenClaw platform
  - Python module + CLI + SKILL.md
  - Tested from live OpenClaw session