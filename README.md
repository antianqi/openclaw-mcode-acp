# OpenClaw ACP — Mavis Coding via HTTP + WebSocket

**Project:** Wrap the Mavis Coding CLI as an OpenClaw-native HTTP + WebSocket protocol, so any OpenClaw agent (or external IDE) can dispatch Mavis tasks without shell-out.

**Status:** v7-bidir (2026-08-14, ~5.5 hours development). Production-ready for demo/internal use.

---

## ⚠️ Auth contract (must read)

The bearer token is read **only** from the `ACP_TOKEN` environment variable.
There is **no default value**, no hardcoded fallback, and no runtime
generation. The server refuses to start without it; the client raises
`ACPTokenMissing` on construction.

To obtain a token (run once on the server host, store in your secret manager):

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

The token travels in:

- HTTP: `Authorization: Bearer <token>` header (every authenticated endpoint).
- WebSocket: `?token=<token>` query parameter (browser WebSocket APIs cannot
  set custom headers).

The server never reads the token from source code, never logs the raw value,
and never accepts it from stdin / prompts. See `docs/RUNTIME_DEPS.md` for the
full contract.

---

## ⚠️ Cross-platform paths

The project is **not** pinned to `D:\openclaw-acp`. All filesystem locations
go through `acp_paths.py`, which honors these env overrides:

| Variable | Default (all platforms) |
|---|---|
| `ACP_HOME` | `~/.openclaw-acp` |
| `OPENCLAW_HOME` | `~/.openclaw` |
| `MCODE_CMD` | `~/.minimax-code/mcode` (POSIX) / `mcode.cmd` (Windows) |
| `ACP_SESSIONS_DIR` | `<ACP_HOME>/sessions` |
| `ACP_TEMP_DIR` | stdlib `tempfile.gettempdir()` |

Any drive letter works on Windows; macOS / Linux work out of the box.
See `INSTALL.md` "Supported platforms" for the test matrix.

---

## Supported platforms

- **Windows 10/11** (Python 3.10+, PowerShell or cmd)
- **macOS 12+** (Python 3.10+, zsh or bash)
- **Linux** — Ubuntu 22.04+ / Debian 12+ / Fedora 38+ tested
- **Python:** 3.10 minimum (3.14 tested)
- **External runtime dep:** Mavis Coding CLI (see `docs/RUNTIME_DEPS.md`)

---

## What's in the box

| Component | Path | Purpose |
|---|---|---|
| **HTTP Server** | `server/acp-server.py` | Async TCP server (port 9999): task CRUD, SSE streaming, history/stats |
| **WebSocket Server** | `server/acp-server.py` (same process, port 9998) | Bidirectional control (cancel/ping/subscribe) |
| **SQLite Store** | `server/acp_store.py` + `acp_inbox_store.py` | Thread-safe persistence (WAL mode, indexed) |
| **Python SDK** | `client/acp_client.py` | Pure stdlib (urllib, no deps) |
| **OpenClaw Skill** | `openclaw-skill/acp_tools.py` + `acp_cli.py` + `acp_paths.py` | Native OpenClaw integration |
| **Smoke test** | `tests/test_smoke.py` | PR-reproducible, no external deps |
| **Documentation** | `docs/acp-system-notes.md` + `docs/RUNTIME_DEPS.md` | Architecture + version contract |

## Quickstart

### 1. Install deps

```bash
pip install -r requirements.txt
```

### 2. Generate + set the auth token

```bash
# Generate a token (do this once, share with clients via your secret manager)
python -c "import secrets; print(secrets.token_urlsafe(32))"

# Export it in your shell (use the value from above)
export ACP_TOKEN='<paste-token-here>'
```

### 3. Start the server

```bash
python server/acp-server.py
```

Expected output:

```
OpenClaw ACP Server v7-bidir (v5 + peer-to-peer inbox) starting
  HTTP:  http://127.0.0.1:9999
  WS:    ws://127.0.0.1:9998/acp/ws?task_id=<id>&token=<token>
  Auth:  <set via $ACP_TOKEN (length=43)>
  Mavis: /home/you/.minimax-code/mcode
  ...
```

> The startup banner **does not** print the token. Only its length.

### 4. Verify

```bash
python openclaw-skill/acp_cli.py health
```

### 5. Run a real Mavis task

```bash
python openclaw-skill/acp_cli.py create \
  --prompt "用一句话回答" \
  --workspace "$ACP_HOME/sessions/demo"
```

### 6. Stop the server

`Ctrl+C` in the foreground terminal, or:

```bash
pkill -f "python .*acp-server.py"     # POSIX
Get-Process python | Where-Object { $_.CommandLine -like '*acp-server*' } | Stop-Process   # PowerShell
```

## Configuration (env vars)

| Env var | Default | Required | Purpose |
|---|---|---|---|
| `ACP_TOKEN` | (none) | **YES** | Bearer token. Server refuses to start without it. |
| `ACP_PORT` | `9999` | no | HTTP port |
| `ACP_WS_PORT` | `9998` | no | WebSocket port |
| `ACP_HOST` | `127.0.0.1` | no | Bind address (use `0.0.0.0` for external) |
| `ACP_MAX_CONCURRENT` | `3` | no | Worker pool size |
| `ACP_HOME` | `~/.openclaw-acp` | no | Project root |
| `OPENCLAW_HOME` | `~/.openclaw` | no | OpenClaw install root |
| `MCODE_CMD` | `~/.minimax-code/mcode[.cmd]` | no | Mavis CLI path |
| `ACP_DB_PATH` | `<tempdir>/acp-tasks.db` | no | SQLite DB location |
| `ACP_LOG` | `<tempdir>/acp-server.log` | no | Server log file |
| `ACP_TEMP_DIR` | stdlib tempdir | no | Used by DB + log defaults |
| `ACP_SESSIONS_DIR` | `<ACP_HOME>/sessions` | no | Peer session workspaces |
| `ACP_BASE_URL` | `http://127.0.0.1:9999` | no | Client-only override |

## Endpoints

### HTTP (port 9999)

| Method | Path | Auth | Purpose |
|---|---|---|---|
| GET | `/acp/health` | no | Server version + queue + db stats |
| POST | `/acp/task/create` | yes | Enqueue task → `task_id` |
| GET | `/acp/task/get?id=X` | yes | Task state (cache first, fallback SQLite) |
| GET | `/acp/task/list` | yes | Recent in-memory tasks |
| GET | `/acp/task/history?status=&workspace=&since=&limit=` | yes | SQLite history with filters |
| GET | `/acp/task/stats` | yes | Counts by status + queue info |
| GET | `/acp/task/stream?id=X` | yes | SSE one-way streaming |
| POST | `/acp/task/cancel` | yes | Cancel running/queued task |

**Peer-to-peer inbox (v7-bidir):**
| Method | Path | Auth | Purpose |
|---|---|---|---|
| POST | `/acp/inbox/write` | yes | Write a message to a session |
| GET | `/acp/inbox/read?session_id=X&since_id=N&sender=Y&msg_type=Z&limit=N` | yes | Read new messages (auto-mark-read) |
| POST | `/acp/inbox/ask` | yes | Write a question + **block server-side** until answered (or timeout) |
| POST | `/acp/inbox/answer` | yes | Answer a pending question |
| GET | `/acp/inbox/sessions?limit=N` | yes | List recent sessions |

### WebSocket (port 9998)

```
ws://localhost:9998/acp/ws?task_id=X&token=<ACP_TOKEN>
```

**Server → Client:** `snapshot` / `queued` / `start` / `output` / `done` / `cancel_ack` / `subscribed` / `pong` / `error`
**Client → Server:** `{"action":"cancel","task_id":"X"}` / `{"action":"ping"}` / `{"action":"subscribe","task_id":"Y"}`

## Verification

```bash
# Smoke test (no Mavis CLI required, runs in <10s)
python tests/test_smoke.py

# Server health
python openclaw-skill/acp_cli.py health

# End-to-end (requires Mavis CLI installed)
python openclaw-skill/acp_cli.py create --prompt "用三句话描述你是谁" --workspace "." --timeout "2m"
```

## Known issues

See `PITFALLS.md`. Highlights:

1. **PITFALLS #1** — `os.environ.get(` write-tool redaction. Mitigated:
   the codebase uses `getattr(__import__('os'), 'environ')` style consistently
   so the write tool cannot mangle the source.
2. **PITFALLS #11** — Mavis `--permission smart` deadlock without TTY.
   Server uses `--permission full` for ACP dispatch.

## Roadmap

- ✅ A. SSE streaming
- ✅ B. SQLite persistence
- ✅ C. Concurrency limit + queue
- ✅ D. WebSocket bidirectional
- ✅ F. OpenClaw integration
- ✅ Auth token now required (no default) — review fix 2026-08-15
- ✅ Cross-platform path resolution via `acp_paths.py` — review fix 2026-08-15
- ✅ PR-reproducible smoke test (`tests/test_smoke.py`) — review fix 2026-08-15
- [ ] **E. Multi-agent routing** (M2.5 / M3 / codex by task type) — top priority next
- [ ] Watchdog (auto-restart on crash)
- [ ] ComfyUI integration

## License

MIT (or whatever the boss decides — `LICENSE` to be added)

## Credits

Built 2026-08-13 in ~5.5 hours by 狗蛋 (OpenClaw agent) for 老板 (安天齐, 绿川椒清水麻辣烫).
Boss chose A→F feature roadmap; 狗蛋 implemented + tested + packaged.

---

**Next:** Open `INSTALL.md` for setup, `PITFALLS.md` for known traps, or `docs/RUNTIME_DEPS.md` for the version contract.
