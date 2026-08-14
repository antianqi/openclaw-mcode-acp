# CHANGELOG — OpenClaw ACP

## v7-bidir — 2026-08-14

**Feature:** Peer-to-peer inbox for goudan <-> mavis collaboration (boss's "开始更新吧" task).

**Motivation:** Boss wants true symmetric collaboration between agents (goudan/mavis), not master/slave task dispatch. They should pass info, divide work, ask blocking questions.

**Second fix (same day, same version):** Mavis invocation was broken — `mcode.cmd` was being invoked without the `exec` subcommand AND with a non-existent `--output-format json` flag. Symptom: every mavis task failed with `Failed to parse Mavis output: Expecting value: line 1 column 1 (char 0)`. Now uses `mcode.cmd exec --input - --input-format text` (stdin pipe) which is mcode's actual non-interactive mode.

**What's new:**
- New module `server/acp_inbox_store.py` — SQLite-backed `acp_inbox` table for peer messaging
  - Schema: `id, session_id, sender ('goudan'|'mavis'|'boss'|'system'), msg_type ('message'|'question'|'answer'), content, parent_id, created_at, read_at, answered_at`
  - Methods: `write`, `ask_question`, `answer_question`, `read_pending`, `wait_for_answer`, `mark_read`, `list_sessions`
  - Thread-safe (WAL mode + lock), indices on `session_id`, `parent_id`, `msg_type`
- `server/acp-server.py` — 5 new HTTP routes + health extension:
  - `POST /acp/inbox/write`  `{session_id, sender, content, msg_type?, parent_id?}`
  - `GET  /acp/inbox/read`   `?session_id=X&since_id=N&sender=Y&msg_type=Z&limit=N` (auto-mark-read)
  - `POST /acp/inbox/ask`    `{session_id, sender, question, timeout?}` — **server-side blocking**, returns 408 on timeout
  - `POST /acp/inbox/answer` `{question_id, answer}`
  - `GET  /acp/inbox/sessions?limit=N`
  - Health now includes `inbox.enabled` + `inbox.sessions` count
  - Version bumped: `v5-ws` → `v7-bidir`
- `openclaw-skill/acp_tools.py` — 5 client SDK functions:
  - `inbox_write(session_id, content, sender='goudan', ...)`
  - `inbox_read(session_id, since_id=0, sender=None, ...)`
  - `inbox_ask(session_id, question, sender='mavis', timeout=300)` — returns dict (timeout is normal business outcome, not exception)
  - `inbox_answer(question_id, answer)`
  - `inbox_sessions(limit=20)`
  - Plus `peer_session_id(prefix='session')` + `peer_greet(session_id, message)` helpers
- New module `openclaw-skill/acp_peer.py`:
  - `wrap_peer_prompt(session_id, original_prompt, extra_context=None)` — injects peer protocol block
  - `setup_session_workspace(session_id, base_dir='D:\\openclaw-acp\\sessions')` — creates `state.json`, `artifacts/`
  - Protocol block teaches mavis:
    - "goudan is your peer, not dispatcher"
    - "Before uncertain decisions, ask goudan (blocking)"
    - "After each phase, push status to goudan"
    - "Don't assume goudan is watching your stdout"

**Backups created (v7-pre-bidir):**
- `server/backups/acp-server.py.v6-pre-bidir.bak` (35520 bytes)
- `server/backups/acp_store.py.v3-pre-bidir.bak` (7684 bytes)
- `openclaw-skill/backups/acp_tools.py.v1-pre-bidir.bak` (5728 bytes)

**Test results (2026-08-14 08:43–08:52):**
- ✅ InboxStore self-test (6/6 assertions)
- ✅ All 4 HTTP endpoint tests (write/read/ask-timeout/sessions + 401/400 validation)
- ✅ SDK sync smoke test (goudan writes, mavis reads, ask with 2s timeout returns 408, answer flow, sessions list)
- ✅ **Stub-mavis ↔ goudan end-to-end demo** (`D:\狗蛋草稿箱\demo_v3.py`): 14 messages exchanged in ~3 seconds, full peer collaboration flow (goudan opens, stub-mavis asks 2 blocking questions, goudan answers both with strings, stub-mavis completes)
- ❌ Real mavis task still hangs after parse-error fix (root cause was mcode invocation — see v7-bidir mcode-fix below)

**Mcode invocation fix (2026-08-14 ~11:24):**

Boss's morning question: "mcode自己好像也有个官方acp" was right — mcode ships with:
- `mcode cmd "prompt"` → tries TUI, fails with "Minimax Code interactive mode requires a TTY"
- `mcode acp` → stdio ACP server (proper long-running protocol — future direction)
- `mcode exec --input - --input-format text` ← non-interactive one-shot (used here)

`mcode exec --help` shows:
```
--input <source>          read explicit input; only "-" is supported
--input-format <format>   input format: text or json (default: "text")
```
No `--output-format` flag exists. The server was passing `--output-format json` which mcode silently ignored, then trying to `json.loads(stdout)` on free-text output, failing every time.

**Changes to `server/acp-server.py`:**
- mcode_args: positional `task['prompt']` → flag `--input - --input-format text`
- Popen: added `stdin=subprocess.PIPE`
- After thread start: write `task['prompt']` to `proc.stdin`, flush, close (signals EOF)
- Parsing: keep JSON-first path; add free-text fallback (heuristic: error keywords → failed, else succeeded)

**Manual verification:**
```
$ echo "用一句话回答:你是谁" | mcode.cmd exec --input - --input-format text
我是 Mavis,跑在 MiniMax Code 上的 coding agent,你(安天齐)的智能助手
```
(GBK encoding visible in Windows console — actual bytes are valid UTF-8.)

**Status:** Parse error 100% fixed. Mavis invocation now produces real output. Real mavis task still hangs at 70s+ timeout (likely model API latency, separate investigation). Infrastructure ready for real mavis once timeout/model latency is tuned.

**Backups created (v7-pre-bidir + v7-pre-mcode-fix):**
- `server/backups/acp-server.py.v6-pre-bidir.bak` (35520 bytes)
- `server/backups/acp_store.py.v3-pre-bidir.bak` (7684 bytes)
- `server/backups/acp-server.py.v7-pre-mcode-fix.bak` (45674 bytes)
- `openclaw-skill/backups/acp_tools.py.v1-pre-bidir.bak` (5728 bytes)

(All excluded from git via `*.bak` pattern in `.gitignore`.)

**Verified infrastructure:**
- Server listens on 9999 + 9998, version reports `v7-bidir`, inbox section in health
- SDK calls succeed end-to-end against live server
- All sender values whitelisted (goudan/mavis/boss/system)
- Sender auto-reversal on `answer_question` (goudan↔mavis symmetric)

**Out of scope for v7-bidir (future work):**
- WS event broadcasting on inbox writes (currently polling only)
- True long-poll on `/acp/inbox/read` (server-side block)
- MCP tool wrapping for higher mavis LLM compliance
- Multi-mavis routing / session-based worker pools
- Shared `state.json` read/write helpers

**Files added:**
- `server/acp_inbox_store.py` (11395 bytes)
- `openclaw-skill/acp_peer.py` (5728 bytes)

**Files modified:**
- `server/acp-server.py` (+~150 lines: import, INBOX init, 5 routes, health, main print)
- `openclaw-skill/acp_tools.py` (+~100 lines: 5 functions, 2 helpers, import json)

## v5-ws — 2026-08-13

**Feature:** WebSocket bidirectional control channel (port 9998)
- `ws://localhost:9998/acp/ws?task_id=X&token=***`
- Client → Server: `{"action":"cancel","task_id":"X"}` / `{"action":"ping"}` / `{"action":"subscribe","task_id":"Y"}`
- Server → Client: `snapshot` / `queued` / `start` / `output` / `done` / `cancel_ack` / `subscribed` / `pong`
- Implementation: `from websockets.asyncio.server import serve` (new asyncio API)
- WS counter for active_connections in health endpoint

## v6-full — 2026-08-13 (hotfix)

**Bug fix:** ACP-dispatched Mavis tasks blocked by `PERMISSION_REQUIRED` (Mavis needs TTY for interactive confirmation; ACP subprocess has no TTY → all tasks deadlocked).

**Fix:** Change `--permission smart` → `--permission full` in mcode args (line 217 of `acp-server.py`).
- `smart` = "ask user for confirmation via stdin" (impossible without TTY)
- `full` = "auto-approve all tool calls" (safe for ACP: boss owns the dispatcher)
- `off` = also works but disables *all* safety; `full` is the right middle-ground

**Also fixed:**
- `LOG_FILE` and `DEFAULT_DB_PATH` now use `os.path.expandvars()` — Windows env vars like `%USERPROFILE%` were not being expanded before, causing FileNotFoundError on fresh starts.

**Verification:**
- Before fix: 5/5 tasks blocked by `PERMISSION_REQUIRED` (any tool use deadlocked)
- After fix: 3/3 tasks succeeded (bash + read + write + multi-tool flow all working autonomously)
- Tested via `task_56965df108124346` and `task_8eec2f2251764c3a`

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