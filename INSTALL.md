# INSTALL — OpenClaw ACP Project

## Requirements

### Supported platforms (tested)

- **Windows 10 / 11** (x64)
- **macOS 12 Monterey or newer** (Intel & Apple Silicon)
- **Linux** — Ubuntu 22.04+ / Debian 12+ / Fedora 38+

### Runtime dependencies

| Dependency | Version | Source | Required? |
|---|---|---|---|
| **Python** | 3.10+ (3.14 tested) | [python.org](https://www.python.org/) | YES |
| **websockets** | `>=16.0,<17` | `pip install -r requirements.txt` | YES |
| **Mavis Coding CLI** (`mcode`) | contract documented in `docs/RUNTIME_DEPS.md` | vendor | YES (for real tasks) |
| **SQLite** | bundled with Python | stdlib | YES |
| **OpenClaw** | any | optional — only for OpenClaw skill consumers | NO |

The smoke test (`tests/test_smoke.py`) does **not** require Mavis Coding —
it validates the auth + endpoint surface end-to-end.

### Auth (required before starting the server)

The bearer token is read from `$ACP_TOKEN`. There is **no default value**.
Generate one on the server host:

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

Copy the output and export it in the shell that will start the server.
Distribute the same value to every client (via your secret manager).

### Filesystem assumptions

There are **no hardcoded paths**. The project root defaults to
`$HOME/.openclaw-acp` on every platform. Override with `$ACP_HOME` to
install anywhere (e.g. `D:\openclaw-acp`, `/opt/openclaw-acp`).

---

## Setup

### 1. Clone / unpack

```bash
# Wherever you want the project — there is no required location
git clone <repo-url> openclaw-acp
cd openclaw-acp
# or, on Windows:
Expand-Archive openclaw-acp.zip
cd openclaw-acp
```

### 2. Install Python dependencies

```bash
pip install -r requirements.txt
```

### 3. Generate + export the auth token

```bash
# Generate once
python -c "import secrets; print(secrets.token_urlsafe(32))"
# → e.g. "xN7rQz9aB2kL8mW3..."

# Export in your shell
export ACP_TOKEN='xN7rQz9aB2kL8mW3...'      # POSIX
$env:ACP_TOKEN = 'xN7rQz9aB2kL8mW3...'      # PowerShell
```

### 4. (Optional) override paths

```bash
# POSIX
export ACP_HOME="$HOME/work/openclaw-acp"     # if not in $HOME/.openclaw-acp
export MCODE_CMD="/opt/mavis/mcode"           # if not in ~/.minimax-code/mcode

# PowerShell
$env:ACP_HOME = 'D:\work\openclaw-acp'
$env:MCODE_CMD = 'D:\tools\mcode.cmd'
```

### 5. Run the smoke test (no Mavis required)

```bash
python tests/test_smoke.py
```

Expected: `ALL CHECKS PASSED`. This validates:

- No hardcoded Windows paths in any Python source file.
- `acp_paths` resolves cross-platform without env vars set.
- Server refuses to start without `ACP_TOKEN`.
- Server boots with `ACP_TOKEN` set, returns 200 on `/acp/health`.
- Authenticated endpoints return 401 without token, 200 with token.
- Inbox write/read roundtrip works.
- Server stdout does not leak the token.

### 6. Start the server

```bash
python server/acp-server.py
```

Expected banner (token length only, never the raw value):

```
OpenClaw ACP Server v7-bidir (v5 + peer-to-peer inbox) starting
  HTTP:  http://127.0.0.1:9999
  WS:    ws://127.0.0.1:9998/acp/ws?task_id=<id>&token=<token>
  Auth:  <set via $ACP_TOKEN (length=43)>
  Mavis: /home/you/.minimax-code/mcode
  ...
```

To run in the background:

```bash
# POSIX
nohup python server/acp-server.py > server.log 2>&1 &

# Windows (PowerShell)
Start-Process python -ArgumentList 'server\acp-server.py' -WindowStyle Hidden
```

### 7. Verify from a client

```bash
# Set the same token
export ACP_TOKEN='xN7rQz9aB2kL8mW3...'     # POSIX
$env:ACP_TOKEN = 'xN7rQz9aB2kL8mW3...'    # PowerShell

# Health check (no auth)
python openclaw-skill/acp_cli.py health

# Create + wait (requires Mavis CLI installed)
python openclaw-skill/acp_cli.py create --prompt "用一句话回答" --workspace "."
python openclaw-skill/acp_cli.py wait --id task_xxxxxxxxxxxxxxxx --timeout 60
```

### 8. Stop the server

```bash
# Foreground: Ctrl+C

# Background:
pkill -f "python .*acp-server.py"                                     # POSIX
Get-Process python | Where-Object { $_.CommandLine -like '*acp-server*' } | Stop-Process  # PowerShell
```

---

## OpenClaw integration (optional)

If you want ACP usable as a native OpenClaw skill, symlink (or copy) the
`openclaw-skill/` directory into your OpenClaw install:

```bash
# POSIX
ln -s "$(pwd)/openclaw-skill" "$HOME/.openclaw/workspace/skills/acp-integration"

# PowerShell
New-Item -ItemType Junction -Path "$env:USERPROFILE\.openclaw\workspace\skills\acp-integration" -Target "$(Resolve-Path .\openclaw-skill)"
```

Then any OpenClaw session can import:

```python
import os
os.environ['ACP_TOKEN'] = '<server token>'
from acp_tools import create_task, wait_task
task_id = create_task("...", workspace="/path")
result = wait_task(task_id)
```

---

## Troubleshooting

**Server exits immediately with "FATAL: $ACP_TOKEN environment variable is not set":**
You forgot step 3. Generate a token and export it.

**`/acp/task/list` returns 401:**
The client's `$ACP_TOKEN` does not match the server's. They must be byte-identical.

**`tests/test_smoke.py` fails on "Test 1: No hardcoded Windows paths":**
You're running an older checkout. Pull the latest — the fix is in commit
"review-fixes-2026-08-15".

**`ModuleNotFoundError: No module named 'acp_paths'`:**
You're running a script outside the project tree. Either `cd` into the
project root, or set `$ACP_HOME` and `$PYTHONPATH`:

```bash
export PYTHONPATH="$ACP_HOME/openclaw-skill:$PYTHONPATH"
```

**`FileNotFoundError: mcode.cmd` (or `mcode`) when creating a task:**
Mavis Coding CLI is not installed at the expected path. Either install it
at `$HOME/.minimax-code/mcode[.cmd]`, or set `$MCODE_CMD` to its actual
location.

**WebSocket connect fails with "unauthorized":**
The token in `?token=` does not match `$ACP_TOKEN`. They must match exactly.

---

## Next steps

- Read `PITFALLS.md` before modifying server code
- Read `docs/acp-system-notes.md` for full architecture
- Read `docs/RUNTIME_DEPS.md` for the version + env contract
- See `tests/` for example test patterns
