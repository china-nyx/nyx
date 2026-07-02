"""Core config — centralized configuration; everything is env-overridable."""
import os
from pathlib import Path

# REPO is auto-detected from this file's location
REPO = Path(__file__).resolve().parent.parent

HOME = Path.cwd().resolve()
if HOME == REPO or str(HOME).startswith(str(REPO) + os.sep):
    raise RuntimeError(f"Runtime root ({HOME}) must not be inside the source repo ({REPO}).")

# Derived paths
LOG_DIR = HOME / "log"
LOG_FILE = LOG_DIR / "nyx.log"
LOG_KEEP_DAYS = 7  # keep last N days of rotated logs
INBOX_DIR = HOME / "mailbox" / "inbox"  # only inbox is used; files ingested to task/
TASK_DIR = HOME / "task"  # task/<tid>/ — per-task persistent state
SKILLS_DIR = HOME / "skills"
SANDBOX_DIR = HOME / "sandbox"
# ── Runtime settings (config/settings.json) ────────────────
_CONFIG_DIR = HOME / "config"
_SETTINGS_FILE = _CONFIG_DIR / "settings.json"
if not _SETTINGS_FILE.exists():
    raise RuntimeError(
        f"Missing settings file: {_SETTINGS_FILE}\n"
        f"See README.md for the required keys."
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


# Logging
KEEP_SESSIONS = int(os.environ.get("NYX_KEEP_SESSIONS") or _settings.get("log", {}).get("keep_sessions", 300))

# Git / entry
ENTRY = os.environ.get("NYX_ENTRY", "app.main:run")

# Runtime directories that must exist
_RUNTIME_DIRS = [INBOX_DIR, TASK_DIR, SKILLS_DIR, SANDBOX_DIR]


def ensure_runtime_dirs():
    """Ensure all runtime directories exist."""
    from sdk.fs import ensure_dir
    for _d in _RUNTIME_DIRS:
        ensure_dir(_d)
