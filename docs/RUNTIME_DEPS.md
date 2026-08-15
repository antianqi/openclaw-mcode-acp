# Runtime Dependencies — Version Contract

OpenClaw ACP is **not** a self-contained binary. It is a thin wrapper over an
external CLI (Mavis Coding) and a peer agent framework (OpenClaw). This file
documents the runtime contracts that callers must satisfy, and the version
pins we test against in CI.

If you change a pin, also update `requirements.txt` and the smoke test.

---

## 1. Python

- **Minimum:** 3.10 (uses `match` patterns + structural typing in acp_inbox_store)
- **Tested on:** 3.14
- **Required for:** everything in this repo

No PyPI deps beyond `websockets`. The HTTP server and client are pure stdlib.

## 2. `websockets` library

- **Pin:** `websockets>=16.0,<17`
- **Why:** v5 introduced the asyncio API (`websockets.asyncio.server.serve`)
  that ACP uses for its bidirectional control channel. v17+ may break compat.
- **Required for:** server only (WS endpoint on port 9998). HTTP/SDK client
  have no `websockets` dependency.

Install:

```bash
pip install -r requirements.txt
```

## 3. Mavis Coding CLI (external)

- **Where:** `$MCODE_CMD` env var, or default `$HOME/.minimax-code/mcode`
  (POSIX) / `$HOME/.minimax-code/mcode.cmd` (Windows).
- **Required subcommand:** `mcode exec --input - --input-format text`
- **Required flag:** `--cwd <workspace>` (we pass workspace per task)
- **Permission mode:** `--permission full` (smart mode deadlocks without TTY;
  see PITFALLS #11)
- **Timeout:** `--timeout <duration>` (e.g. `5m`)
- **Encoding:** server reads stdout/stderr as UTF-8 (`encoding='utf-8'`)

If `mcode.cmd` is missing, `acp_paths.resolve_mcode_cmd()` returns the
default path string but the server's first subprocess call will fail. The
smoke test does NOT require mcode (it tests the auth + endpoint surface
without spawning any subprocess).

**To install Mavis Coding** (out of scope for this repo): see the official
Mavis Coding docs. Without it, the server starts but every `/acp/task/create`
returns `failed` with `FileNotFoundError` on `mcode.cmd`.

## 4. OpenClaw Skill: `mavis-coding` (peer install)

- **Where:** `$OPENCLAW_HOME/skills/mavis-coding/` (default `~/.openclaw/skills/mavis-coding/`)
- **Required for:** nothing at runtime. The ACP server is now self-contained
  inside this repo. The legacy install location is only checked by
  `acp_paths.mavis_coding_skill_dir()` as a fallback for the SDK.
- **Recommendation:** if you previously installed ACP into the OpenClaw
  skills dir, remove that old copy — the canonical install is now
  `<ACP_HOME>/`.

## 5. `acp-server.py` callers

When the server starts, it reads these env vars (all required unless noted):

| Variable | Required | Default | Notes |
|---|---|---|---|
| `ACP_TOKEN` | **YES** | (none) | Server refuses to start if missing. |
| `ACP_PORT` | no | 9999 | HTTP port. |
| `ACP_WS_PORT` | no | 9998 | WebSocket port. |
| `ACP_HOST` | no | 127.0.0.1 | Bind address (set `0.0.0.0` for external clients). |
| `ACP_MAX_CONCURRENT` | no | 3 | Worker pool size. |
| `ACP_HOME` | no | `~/.openclaw-acp` | Project root (cross-platform). |
| `ACP_DB_PATH` | no | `<tempdir>/acp-tasks.db` | SQLite DB. |
| `ACP_LOG` | no | `<tempdir>/acp-server.log` | Server log. |
| `ACP_TEMP_DIR` | no | stdlib `tempfile.gettempdir()` | Used for default DB + log. |
| `ACP_SESSIONS_DIR` | no | `<ACP_HOME>/sessions` | Peer session workspaces. |
| `MCODE_CMD` | no | `$HOME/.minimax-code/mcode` | Mavis CLI path. |
| `OPENCLAW_HOME` | no | `~/.openclaw` | OpenClaw install root. |
| `ACP_BASE_URL` | no | `http://127.0.0.1:9999` | Client-only override. |

---

## 6. Smoke test

`tests/test_smoke.py` validates this contract end-to-end:

1. All Python source files import without `D:\` / `%USERPROFILE%` / raw `r'...'` Windows-path literals (cross-platform source check).
2. `acp_paths.resolve_acp_home()` works on the current OS without setting `ACP_HOME`.
3. `acp_paths.resolve_mcode_cmd()` returns a string on every platform.
4. Server **refuses to start** when `ACP_TOKEN` is missing.
5. Server **starts** when `ACP_TOKEN` is set, and `/acp/health` returns 200.
6. Authenticated endpoints return 401 without a token.
7. Authenticated endpoints return 200 with the correct token.
8. WebSocket endpoint accepts the token via query param (browser compat).
9. Inbox write/read roundtrip works.

Run with:

```bash
cd $ACP_HOME
python -m tests.test_smoke
# or:
python tests/test_smoke.py
```

No external services required (mcode, OpenClaw gateway, network). Runs in
under 10s on a developer laptop.
