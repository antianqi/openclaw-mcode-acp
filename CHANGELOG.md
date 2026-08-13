# CHANGELOG — OpenClaw ACP

## v5-ws — 2026-08-13

**Feature:** WebSocket bidirectional control channel (port 9998)
- `ws://localhost:9998/acp/ws?task_id=X&token=***`
- Client → Server: `{"action":"cancel","task_id":"X"}` / `{"action":"ping"}` / `{"action":"subscribe","task_id":"Y"}`
- Server → Client: `snapshot` / `queued` / `start` / `output` / `done` / `cancel_ack` / `subscribed` / `pong`
- Implementation: `from websockets.asyncio.server import serve` (new asyncio API)
- WS counter for active_connections in health endpoint

## v4-queue — 2026-08-13

**Feature:** Worker pool + FIFO queue + `queued` status
- `MAX_CONCURRENT=3` workers (env-tunable)
- New task status: `queued` (waiting in FIFO)
- SSE `queued` event with position
- `/acp/health` shows `queue.size / active / max_concurrent / pending_ids`

## v3-sqlite — 2026-08-13

**Feature:** SQLite-backed persistence
- New module: `acp_store.py` (WAL mode + indices)
- DB path: `%TEMP%\acp-tasks.db`
- New endpoints: `GET /acp/task/history` (with status/workspace/since filters), `GET /acp/task/stats`
- Task state survives server restart

## v2-sse — 2026-08-13

**Feature:** Server-Sent Events (SSE) one-way streaming
- New endpoint: `GET /acp/task/stream?id=X` (text/event-stream)
- Events: `snapshot` / `start` / `output` / `done`
- Keepalive every 15s
- Queue kept alive 5 min after `done` for late reconnects
- UTF-8 stdout/stderr decoding (was system GBK → garbled)

## v1 — 2026-08-13 (initial)

**Feature:** Basic HTTP server (5 endpoints)
- `GET /acp/health`
- `POST /acp/task/create` → returns `task_id`
- `GET /acp/task/get?id=X` — poll task status
- `GET /acp/task/list` — recent in-memory tasks
- `POST /acp/task/cancel` — cancel running task
- Auth: Bearer token
- Single-threaded `HTTPServer` (replaced with `ThreadingHTTPServer` in v5)

---

## Bundled OpenClaw integration (F)

**openclaw-skill/** — OpenClaw-native Python + CLI wrappers
- `acp_tools.py` — Python module: `create_task / wait_task / stream_task / health / stats / history`
- `acp_cli.py` — CLI: argparse-based, subcommands for all operations
- `SKILL.md` — OpenClaw skill metadata + usage docs

---

## Project packaging — 2026-08-13 (later same day)

Everything copied to `D:\openclaw-acp\` as a self-contained project:
- Server + SQLite store + Python SDK
- OpenClaw skill wrappers
- 6 end-to-end test scripts (openclaw-restart / nuke-restart / b-test / c-test / d-test / sse-test)
- 2 utility scripts (fix-server-auth / fix-server-auth-v2)
- Full backups (v1→v5 server.py)
- Top-level README + INSTALL + PITFALLS + this CHANGELOG
- Topic file `docs/acp-system-notes.md` (durable knowledge)

## Roadmap

- [ ] **E. Multi-agent routing** (M2.5 / M3 / codex by task type)
- [ ] Watchdog (auto-restart on crash)
- [ ] ComfyUI integration via ACP
- [ ] Multi-tenant auth (OAuth / API keys)
- [ ] Prometheus metrics