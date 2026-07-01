"""Core config — centralized configuration; everything is env-overridable."""
import os
from pathlib import Path

# CODE is auto-detected from this file's location
CODE = Path(__file__).resolve().parent.parent

_home_env = os.environ.get("NYX_HOME")
if _home_env:
    HOME = Path(_home_env)
else:
    HOME = Path.cwd().resolve()
# Always export so child processes (smoke, checks) inherit the correct home
os.environ["NYX_HOME"] = str(HOME)
if HOME == CODE or str(HOME).startswith(str(CODE) + os.sep):
    raise RuntimeError(f"Runtime root ({HOME}) must not be inside the source repo ({CODE}).")

# Derived paths
WORKTREES = HOME / "worktree"
LOG_DIR = HOME / "log"
LOG_FILE = LOG_DIR / "nyx.log"
LOG_KEEP_DAYS = 7  # keep last N days of rotated logs
INBOX_DIR = HOME / "mailbox" / "inbox"  # only inbox is used; files ingested to task/
TASK_DIR = HOME / "task"  # task/<tid>/ — per-task persistent state
SKILLS_DIR = HOME / "skills"
SANDBOX_DIR = HOME / "sandbox"
SRC_LINK = HOME / "sandbox" / "src"  # symlink -> CODE, so solver sees source under sandbox/

# ── Runtime settings ($NYX_HOME/config/settings.json) ────────────────
_CONFIG_DIR = HOME / "config"
_SETTINGS_FILE = _CONFIG_DIR / "settings.json"
if not _SETTINGS_FILE.exists():
    raise RuntimeError(
        f"Missing settings file: {_SETTINGS_FILE}\n"
        f"Create it with the required keys (see config/settings.json.example)"
    )
import json as _json
try:
    _settings = _json.loads(_SETTINGS_FILE.read_text())
except Exception as e:
    raise RuntimeError(f"Invalid settings file {_SETTINGS_FILE}: {e}") from e

# LLM (single endpoint, OpenAI-compatible)
LLM_BASE_URL = _settings.get("llm", {}).get("base_url", "")
LLM_MODEL = _settings.get("llm", {}).get("model", "")
LLM_API_KEY = _settings.get("llm", {}).get("api_key", "")
if not LLM_BASE_URL or not LLM_MODEL:
    raise RuntimeError(f"settings.json must have 'llm.base_url' and 'llm.model'")
LLM_TIMEOUT = int(os.environ.get("NYX_LLM_TIMEOUT") or _settings.get("llm", {}).get("timeout", 300))


# Smoke check limits
SANDBOX_TIMEOUT = int(os.environ.get("NYX_SANDBOX_TIMEOUT") or _settings.get("sandbox", {}).get("timeout", 180))
SANDBOX_MEM_MB = int(os.environ.get("NYX_SANDBOX_MEM_MB") or _settings.get("sandbox", {}).get("mem_mb", 4096))

# Logging
LOG_MAX_MB = int(os.environ.get("NYX_LOG_MAX_MB") or _settings.get("log", {}).get("max_mb", 50))
KEEP_SESSIONS = int(os.environ.get("NYX_KEEP_SESSIONS") or _settings.get("log", {}).get("keep_sessions", 300))

# Git / entry
ENTRY = os.environ.get("NYX_ENTRY", "app.agent:run")

# Ensure directories exist
for _d in [HOME, WORKTREES, INBOX_DIR, TASK_DIR, LOG_DIR]:
    _d.mkdir(parents=True, exist_ok=True)
