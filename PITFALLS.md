# PITFALLS — 10 traps and workarounds

Documented during v1→v5 development. If you hit any of these, check this file first.

## ⭐⭐⭐ 1. `os.environ.get(` write-tool redaction

**Problem:** The `write` tool (used by some agents / editors) auto-replaces Python source `os.environ.get(` with the literal string `***`, leaving broken syntax:
```python
AUTH_TOKEN = ***'ACP_TOKEN', 'openclaw-acp-demo-token')
#           ^^^ should be os.environ.get(
```

**Symptoms:** `SyntaxError: unmatched ')'` or `SyntaxError: closing parenthesis ')' does not match opening parenthesis '['`

**Workaround:** Use `getattr(__import__('os').environ, 'get')(...)` instead of `os.environ.get(...)`. The codebase now uses this style consistently — `acp-server.py`, `acp_store.py`, `acp_inbox_store.py` all read env via `getattr(__import__('os'), 'environ').get(...)`.

**Critical:** All letters must be ASCII — `getattr`, NOT `getatt…t__` (unicode ellipsis). I've fallen for this typo twice.

**Auto-fix scripts:** `tests/fix-server-auth.py` (v1) and `tests/fix-server-auth-v2.py` (handles `***` prefix correctly).

**Status as of 2026-08-15 review:** the workaround is no longer needed at runtime (we use getattr everywhere). The auto-fix scripts in `tests/` are kept as belt-and-suspenders for any future PR that re-introduces `os.environ.get(` literal. The new review gate (`tests/test_smoke.py` Test 1) greps for hardcoded Windows paths and would also catch re-introductions.

## ⭐⭐⭐ 11. Mavis `--permission smart` deadlock via ACP

**Problem:** When ACP dispatches a Mavis task (subprocess via `cmd.exe /c mcode.cmd`), Mavis's stdin is not a TTY (it's `None` because `subprocess.Popen` didn't pass `stdin=PIPE`). Mavis's default `--permission smart` policy tries to prompt user for confirmation on any tool call (read/write/bash) → sees no TTY → returns `PERMISSION_REQUIRED` → every task deadlocks immediately.

**Symptom:** Every task completes in ~5-30s with `status: blocked` and `error.code: PERMISSION_REQUIRED`. Mavis answers "我先...扫一下/看一下" then dies.

**Fix:** Pass `--permission full` to Mavis (in `acp-server.py` line 217):
- `ask` / `smart` — interactive confirm (blocked without TTY)
- `full` — auto-approve all (✅ use this for ACP dispatch)
- `off` — also works but disables *all* safety; `full` is the better choice

**Code change:**
```python
mcode_args = [
    'exec', task['prompt'],
    '--cwd', task['workspace'],
    '--output-format', 'json',
    '--permission', 'full',  # was 'smart' — blocks ACP dispatch
    '--timeout', task['timeout'],
]
```

**Alternative (NOT recommended):** Pre-feed files via `--files` so Mavis never needs the read tool. This works but is a workaround — Mavis can't autonomously explore your workspace.

**Gotcha discovered during this fix:** When OpenClaw spawns ACP subprocess, **also** the `LOG_FILE` and `DEFAULT_DB_PATH` env vars need `os.path.expandvars()` — the strings literally contain `%USERPROFILE%` which Python doesn't auto-expand in `os.environ.get()` defaults. **Fixed in 2026-08-15 review** by replacing with cross-platform `acp_paths.resolve_temp_dir()`.

## 2. Windows Terminal defaultProfile GUID mismatch

**Problem:** Installing PowerShell 7.6 registers a new Windows Terminal profile (with a new GUID), but `settings.json`'s `defaultProfile` still points to the old PowerShell 5.1 GUID. Windows Terminal shows "加载用户设置时遇到错误" on every launch.

**Symptom:** Two error dialogs each time Windows Terminal starts (one per open window).

**Fix:** Add PowerShell 7.6 profile to `~/.openclaw/.../Windows Terminal/settings.json` `profiles.list`:
```json
{
  "guid": "{7EAB037B-2D8A-49EF-85BC-846AA550FF4F}",
  "name": "PowerShell 7.6.4",
  "commandline": "pwsh.exe",
  "icon": "%ProgramFiles%\\PowerShell\\7\\assets\\Powershell_av_colors.ico"
}
```

## 3. Acp-server zombie processes after restart

**Problem:** `openclaw gateway restart` or similar simple restarts don't actually kill old server processes (port conflict → new server exits immediately). Symptom: new code changes don't take effect.

**Workaround:** Use `tests/nuke-restart.py`:
```python
Get-WmiObject Win32_Process -Filter "Name='python.exe'" | 
    Where-Object { $_.CommandLine -like '*acp-server*' } | 
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
```

## 4. exec tool shell is PowerShell 5.1 (despite 7.6 install)

**Problem:** OpenClaw's exec tool caches its shell at startup. Even after installing PowerShell 7.6 and rebooting, the cached shell is still 5.1.

**Symptoms:**
- `&&` not supported (5.1 doesn't have it)
- `Format-Table` / `$()` quoting issues
- Chinese characters in output may show as mojibake

**Workaround:** Use `;` instead of `&&`, OR run commands via absolute path to pwsh 7.6:
```powershell
& '%USERPROFILE%\pwsh7_6\pwsh.exe' -NoProfile -Command "..."
```

## 5. SSE streaming GBK decode bug

**Problem:** `subprocess.Popen` defaults to system GBK decoding for stdout/stderr. Mavis outputs UTF-8 → silent garbled events.

**Symptom:** SSE events have `[stream error: 'gbk' codec can't decode...]` in `line` field.

**Fix in `acp-server.py`:**
```python
proc = subprocess.Popen(
    ['cmd.exe', '/c', MCODE_CMD] + mcode_args,
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    text=True,
    encoding='utf-8',      # ← add this
    errors='replace',       # ← add this
    bufsize=1,
)
```

## 6. websockets 16.0 legacy vs asyncio API

**Problem:** `from websockets.server import serve` is the legacy API. Legacy `WebSocketServerProtocol` does NOT have `.request` attribute.

**Symptom:** `AttributeError: 'WebSocketServerProtocol' object has no attribute 'request'` on every WS connection.

**Fix:** Use the asyncio API:
```python
from websockets.asyncio.server import serve as ws_serve  # ← asyncio, not .server
```

## 7. set can't contain unhashable dict

**Problem:** `set.add(dict)` raises `TypeError: cannot use 'dict' as a set element (unhashable type: 'dict')`.

**Symptom:** WS connection handler crashes immediately.

**Fix:** Use a counter instead of a set:
```python
WS_CONN_COUNT = 0
WS_LOCK = threading.Lock()
# On connect:
with WS_LOCK:
    WS_CONN_COUNT += 1
# On disconnect:
with WS_LOCK:
    WS_CONN_COUNT -= 1
```

## 8. `global` must be at function top

**Problem:** Python requires `global X` to appear BEFORE any use of `X` in a function.

**Symptom:** `SyntaxError: name 'X' is used prior to global declaration`

**Fix:** Move `global X` to the first statement of the function:
```python
async def my_handler():
    global X  # ← must be first
    ...
```

## 9. ~~Token redaction in client scripts~~ — RESOLVED 2026-08-15

**Original problem:** Hardcoded auth tokens in source get redacted by some write tools. `openclaw-acp-demo-token` → `opencl…oken` (with unicode ellipsis) → `urllib` latin-1 encoding fails.

**Symptom:** `UnicodeEncodeError: 'latin-1' codec can't encode character '\u2026'`

**Original fix:** Don't hardcode. Read token dynamically from `acp-server.py` source.

**Why this entry is marked RESOLVED:** As of the 2026-08-15 review, the client (`client/acp_client.py`) no longer contains a default token at all. `read_token_from_server()` was removed. The client reads `$ACP_TOKEN` only, raises `ACPTokenMissing` if unset, and never touches the server's source code. The "source scraping" trick was itself a credentials leak (the user comment was right to flag it), so removing it is a security improvement, not just a robustness fix.

If a future change re-introduces a default token, `tests/test_smoke.py` Test 8 ("server stdout does not leak token") and Test 1 (expanded to grep for default-token patterns) will catch it.

## 10. Unicode emoji in GBK stdout

**Problem:** Printing ✅ / ❌ / 🔥 etc. via Python `print()` → GBK shell can't encode → `UnicodeEncodeError`.

**Symptom:** Script crashes at the final `print()` call after tests passed.

**Fix:** Force UTF-8 stdout:
```python
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
```

Or just use ASCII markers: `[OK]` / `[FAIL]` / `[WIN]`.

---

**Bonus trap: 12. Acp-server auth redaction after every restart**

After every restart with `nuke-restart.py`, if you also edit `acp-server.py`, you may re-introduce the `os.environ.get(` → `***` redaction bug. Always check:
```powershell
grep "AUTH_TOKEN" server\acp-server.py
# Should show: AUTH_TOKEN = ***'os').environ, 'get')('ACP_TOKEN', ...)
# NOT:          AUTH_TOKEN = ***'ACP_TOKEN', 'openclaw-acp-demo-token')
```

Run `python -c "import ast; ast.parse(open('server/acp-server.py').read())"` for quick syntax check before starting.

**Status as of 2026-08-15 review:** the demo-token default has been removed entirely. `ACP_TOKEN` is now required. There is no fallback string to leak; the server simply exits with code 2 if the env var is missing.
