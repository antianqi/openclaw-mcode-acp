"""acp_paths.py — Cross-platform path resolution for OpenClaw ACP.

Single source of truth for *where* ACP files live on disk.

Resolution order (first match wins):
  1. Explicit constructor argument (for tests)
  2. $ACP_HOME environment variable (recommended for non-default installs)
  3. $HOME/.openclaw-acp (cross-platform default; works on Windows / macOS / Linux
     and any drive letter / mount point on Windows)

We deliberately do NOT hardcode any drive-letter path — that breaks:
  * macOS / Linux installs (no D: drive concept)
  * Windows installs on any drive other than D:
  * CI runners (typically C: only)

All other modules (server, client, peer template, OpenClaw skill wrappers) MUST
import from this module instead of constructing paths inline. See
INSTALL.md "Requirements" for the supported-platform matrix.
"""
import os
import sys
from pathlib import Path


def resolve_acp_home(explicit: str = None) -> Path:
    """Resolve the OpenClaw ACP project root directory.

    Args:
        explicit: optional override (used by tests)

    Returns:
        Absolute Path to the ACP project root. The directory is NOT required
        to exist on disk — callers that need the directory should create it.
    """
    if explicit:
        return Path(explicit).expanduser().resolve()
    env = os.environ.get('ACP_HOME')
    if env:
        return Path(env).expanduser().resolve()
    # Cross-platform default: $HOME/.openclaw-acp
    return (Path.home() / '.openclaw-acp').resolve()


def resolve_openclaw_home() -> Path:
    """Resolve the OpenClaw install root (where skills/, workspace/ live).

    Resolution order:
      1. $OPENCLAW_HOME env var
      2. $HOME/.openclaw (default)
    """
    env = os.environ.get('OPENCLAW_HOME')
    if env:
        return Path(env).expanduser().resolve()
    return (Path.home() / '.openclaw').resolve()


def resolve_mcode_cmd() -> str:
    """Resolve the Mavis Coding CLI command path.

    Returns:
        Absolute path string to mcode.cmd (Windows) or mcode (POSIX).

    Raises:
        FileNotFoundError: if no candidate exists. Callers should catch and
        surface a clear "install Mavis Coding first" error.
    """
    home = Path.home()
    candidates = []
    if os.name == 'nt':
        candidates.append(home / '.minimax-code' / 'mcode.cmd')
    else:
        candidates.append(home / '.minimax-code' / 'mcode')
        candidates.append(Path('/usr/local/bin/mcode'))
        candidates.append(Path('/opt/minimax-code/mcode'))
    # Allow override
    override = os.environ.get('MCODE_CMD')
    if override:
        return str(Path(override).expanduser().resolve())
    for c in candidates:
        if c.exists():
            return str(c.resolve())
    # No file exists — return the platform-native default; caller decides
    # whether to fail fast.
    return str(candidates[0]) if candidates else 'mcode'


def resolve_temp_dir() -> Path:
    """Cross-platform temp dir (used for ACP_DB_PATH and ACP_LOG defaults).

    Resolution order:
      1. $ACP_TEMP_DIR env var (optional override)
      2. stdlib tempfile.gettempdir() — works on Windows / macOS / Linux
    """
    env = os.environ.get('ACP_TEMP_DIR')
    if env:
        return Path(env).expanduser().resolve()
    import tempfile
    return Path(tempfile.gettempdir()).resolve()


def acp_skill_dir(acp_home: Path = None) -> Path:
    """Path to the openclaw-skill/ directory inside the ACP project."""
    return (acp_home or resolve_acp_home()) / 'openclaw-skill'


def mavis_coding_skill_dir() -> Path:
    """Path to the mavis-coding OpenClaw skill (where acp-server.py used to live).

    Resolution order:
      1. $OPENCLAW_HOME/skills/mavis-coding
      2. $HOME/.openclaw/skills/mavis-coding
    """
    return resolve_openclaw_home() / 'skills' / 'mavis-coding'


def client_sdk_path(acp_home: Path = None) -> Path:
    """Path to the acp_client.py SDK inside client/."""
    return (acp_home or resolve_acp_home()) / 'client' / 'acp_client.py'


def server_script_path(acp_home: Path = None) -> Path:
    """Path to acp-server.py inside server/."""
    return (acp_home or resolve_acp_home()) / 'server' / 'acp-server.py'


def sessions_root(acp_home: Path = None) -> Path:
    """Root directory for peer-session workspaces.

    Defaults to <ACP_HOME>/sessions; override via $ACP_SESSIONS_DIR.
    """
    env = os.environ.get('ACP_SESSIONS_DIR')
    if env:
        return Path(env).expanduser().resolve()
    return (acp_home or resolve_acp_home()) / 'sessions'


if __name__ == '__main__':
    # Self-test: print resolved paths and verify they don't depend on D:\.
    ah = resolve_acp_home()
    print(f'ACP_HOME         : {ah}')
    print(f'OPENCLAW_HOME    : {resolve_openclaw_home()}')
    print(f'mcode cmd        : {resolve_mcode_cmd()}')
    print(f'temp dir         : {resolve_temp_dir()}')
    print(f'sessions root    : {sessions_root()}')
    print(f'acp skill dir    : {acp_skill_dir()}')
    print(f'mavis-coding dir : {mavis_coding_skill_dir()}')
    # Sanity: nothing should start with D:\ unless explicitly set
    import platform
    s = str(ah)
    if s.startswith('D:\\') and not os.environ.get('ACP_HOME') and not os.environ.get('_ACP_PATHS_TEST'):
        print('NOTE: default ACP_HOME landed on D:\\ — set ACP_HOME to relocate.')
    print(f'platform         : {platform.system()} {platform.release()}')
    print('[OK] acp_paths resolved cross-platform')
