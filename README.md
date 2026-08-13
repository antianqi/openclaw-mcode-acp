# OpenClaw ACP — Mavis Coding via HTTP + WebSocket

**Project:** Wrap the Mavis Coding CLI as an OpenClaw-native HTTP + WebSocket protocol, so any OpenClaw agent (or external IDE) can dispatch Mavis tasks without shell-out.

**Status:** v5-ws (2026-08-13, ~5.5 hours development). Production-ready for demo/internal use.

> **Note:** Repository renamed from `openclaw-acp` to `OpenClaw-mcode-ACP` on 2026-08-13. All commits, stars, and links are preserved (301 redirect from old URL).

---

## What's in the box

| Component | Path | Purpose |
|---|---|---|
| **HTTP Server** | `server/acp-server.py` | Async TCP server (port 9999): task CRUD, SSE streaming, history/stats |
| **SQLite Store** | `server/acp_store.py` | Thread-safe persistence (WAL mode, indexed) |
| **Python SDK** | `client/acp_client.py` | Pure stdlib (urllib, no deps), with token auto-read |
| **OpenClaw Skill** | `openclaw-skill/acp_tools.py` + `acp_cli.py` | Native OpenClaw integration |
| **WebSocket Server** | `server/acp-server.py` (same process, port 9998) | Bidirectional control (cancel/ping/subscribe) |
| **End-to-end Tests** | `tests/` | B/C/D/E/F feature verification scripts |
| **Documentation** | `docs/acp-system-notes.md` | Full topic file with architecture + 10 known pitfalls |

## Quickstart (60 seconds)

```powershell
# 1. Install deps (only websockets needed)
pip install -r requirements.txt

# 2. Start the server (runs in background, logs to %TEMP%\acp-server.log)
.\scripts\start_server.bat

# 3. Verify it's up
python openclaw-skill\acp_cli.py health

# 4. Run a real Mavis task
python openclaw-skill\acp_cli.py create --prompt "用一句话回答" --workspace "C:\path\to\workspace"
# Output: task_xxxxxxxxxxxxxxxx

# 5. Stream events live
python openclaw-skill\acp_cli.py stream --id task_xxxxxxxxxxxxxxxx

# 6. Stop the server (graceful)
.\scripts\stop_server.bat
```

Or from Python:

```python
import sys; sys.path.insert(0, 'openclaw-skill')
from acp_tools import create_task, wait_task
task_id = create_task("用一句话回答", workspace="C:\\path\\to\\workspace")
result = wait_task(task_id, timeout=120)
print(result['answer'])
```

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
| GET | `/acp/task/stream?id=X` | yes | **SSE one-way streaming** |
| POST | `/acp/task/cancel` | yes | Cancel running/queued task |

### WebSocket (port 9998)

```
ws://localhost:9998/acp/ws?task_id=X&token=***
```

**Server → Client:** `snapshot` / `queued` / `start` / `output` / `done` / `cancel_ack` / `subscribed` / `pong` / `error`
**Client → Server:** `{"action":"cancel","task_id":"X"}` / `{"action":"ping"}` / `{"action":"subscribe","task_id":"Y"}`

## Project structure

```
D:\openclaw-acp\
├── README.md                          # this file
├── INSTALL.md                         # setup guide
├── PITFALLS.md                        # 10 traps + workarounds
├── CHANGELOG.md                       # v1 → v5 history
├── requirements.txt                   # websockets>=16.0
├── scripts\
│   ├── start_server.bat               # Windows convenience
│   └── stop_server.bat
├── server\
│   ├── acp-server.py                  # v5-ws (34K)
│   ├── acp_store.py                   # SQLite layer (7K)
│   ├── README.md                      # server docs
│   └── backups\                       # v1-v5 rollback chain
├── client\
│   └── acp_client.py                  # Python SDK (urllib)
├── openclaw-skill\                    # F: native OpenClaw integration
│   ├── SKILL.md
│   ├── acp_tools.py
│   └── acp_cli.py
├── tests\                             # end-to-end test scripts
│   ├── nuke-restart.py                # kill zombies + restart
│   ├── fix-server-auth.py            # fix redaction bug
│   ├── fix-server-auth-v2.py
│   ├── openclaw-restart.py            # legacy restart
│   ├── sse-test.py                    # SSE end-to-end
│   ├── b-test_sqlite_persistence.py  # B test
│   ├── c-test_concurrency_queue.py   # C test
│   └── d-test_websocket.py            # D test
└── docs\
    └── acp-system-notes.md            # full topic file (8K)
```

## Configuration (env vars)

| Env var | Default | Purpose |
|---|---|---|
| `ACP_PORT` | 9999 | HTTP port |
| `ACP_WS_PORT` | 9998 | WebSocket port |
| `ACP_HOST` | 127.0.0.1 | Bind address |
| `ACP_MAX_CONCURRENT` | 3 | Worker pool size |
| `ACP_TOKEN` | `openclaw-acp-demo-token` | Auth token |
| `ACP_DB_PATH` | `%TEMP%\acp-tasks.db` | SQLite DB location |
| `ACP_LOG` | `%TEMP%\acp-server.log` | Server log file |

## Verification (sanity-check after setup)

```powershell
# 1. Server health
python openclaw-skill\acp_cli.py health
# Should show: "version": "v5-ws", "ws": {"port": 9998}

# 2. End-to-end: create → wait
python openclaw-skill\acp_cli.py create --prompt "用三句话描述你是谁" --workspace "." --timeout "2m"
# Copy the task_id, then:
python openclaw-skill\acp_cli.py wait --id task_xxxxxxxxxxxxxxxx --timeout 60
# Should return: {"status": "succeeded", "answer": "..."}
```

## Known issues

See [PITFALLS.md](PITFALLS.md) for the full list of 10 gotchas. The two most common:

1. **`write` tool redaction of `os.environ.get(`** — fixes included in `tests/fix-server-auth*.py`
2. **WebSocket 16 API** — use `websockets.asyncio.server`, not legacy `websockets.server`

## Roadmap

- ✅ A. SSE streaming
- ✅ B. SQLite persistence
- ✅ C. Concurrency limit + queue
- ✅ D. WebSocket bidirectional
- ✅ F. OpenClaw integration
- [ ] **E. Multi-agent routing** (M2.5 / M3 / codex by task type) — top priority next
- [ ] Watchdog (auto-restart on crash)
- [ ] ComfyUI integration

## License

MIT (or whatever the boss decides — `LICENSE` to be added)

## Credits

Built 2026-08-13 in ~5.5 hours by 狗蛋 (OpenClaw agent) for 老板 (安天齐, 绿川椒清水麻辣烫).
Boss chose A→F feature roadmap; 狗蛋 implemented + tested + packaged.

---

**Next step:** Open [INSTALL.md](INSTALL.md) for setup, or [PITFALLS.md](PITFALLS.md) for known traps.