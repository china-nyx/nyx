"""Core config — all settings in one place.  Pure data, no side-effects on import."""
import json
import os
from pathlib import Path

from pydantic import BaseModel, Field, field_validator

from app.hooks.compaction import CompactionSettings


class Config(BaseModel):
    """NYX runtime configuration.

    Created in boot.py from ``config/settings.json``.
    All app modules read the singleton via ``from app.config import config``.
    """

    # ── paths ──────────────────────────────────────────────────
    repo: Path = Field(..., description="Source-code repo (read-only)")
    home: Path = Field(..., description="Runtime working directory (read-write)")

    # ── LLM ────────────────────────────────────────────────────
    llm_base_url: str
    llm_model: str
    llm_api_key: str = ""
    llm_timeout: int = 300

    # ── logging ────────────────────────────────────────────────
    log_keep_days: int = 7

    # ── session retention ──────────────────────────────────────
    keep_sessions: int = 300

    # ── agent behavior ─────────────────────────────────────────
    req_retry_sec: int = 25           # seconds between retry attempts for same task
    daily_reflect_sec: int = 86400     # seconds between daily reflection cycles (1 day)
    context_window: int = 256_000      # LLM context window size in tokens

    # ── compaction ─────────────────────────────────────────────
    compaction_settings: CompactionSettings = Field(
        default_factory=CompactionSettings,
        description="Context compaction behaviour",
    )

    # ── validation ─────────────────────────────────────────────
    @field_validator("home")
    @classmethod
    def _check_home_not_in_repo(cls, v: Path, info):
        repo = info.data.get("repo")
        if repo and (v == repo or str(v).startswith(str(repo) + os.sep)):
            raise ValueError(f"Runtime root ({v}) must not be inside the source repo ({repo})")
        return v

    # ── derived paths (read-only properties) ───────────────────
    @property
    def log_dir(self) -> Path:
        return self.home / "log"

    @property
    def log_file(self) -> Path:
        return self.log_dir / "nyx.log"

    @property
    def inbox_dir(self) -> Path:
        return self.home / "mailbox" / "inbox"

    @property
    def task_dir(self) -> Path:
        return self.home / "task"

    @property
    def skills_dir(self) -> Path:
        return self.home / "skills"

    @property
    def memory_dir(self) -> Path:
        return self.home / "memory"

    @property
    def projects_dir(self) -> Path:
        return self.home / "projects"

    @property
    def temp_dir(self) -> Path:
        return self.home / "temp"

    @property
    def runtime_dirs(self) -> list:
        return [self.log_dir, self.inbox_dir, self.task_dir, self.skills_dir,
                self.memory_dir, self.projects_dir, self.temp_dir]

    @classmethod
    def from_settings(cls, *, repo: Path, home: Path):
        """Build Config from ``config/settings.json``.

        Raises RuntimeError if the file is missing or invalid.
        """
        sf = home / "config" / "settings.json"
        if not sf.exists():
            raise RuntimeError(
                f"Missing settings file: {sf}\n"
                f"See README.md for the required keys."
            )
        try:
            raw = json.loads(sf.read_text(encoding="utf-8"))
        except Exception as e:
            raise RuntimeError(f"Invalid settings file {sf}: {e}") from e

        llm = raw.get("llm", {})
        log_ = raw.get("log", {})
        session_ = raw.get("session", {})

        return cls(
            repo=repo,
            home=home,
            llm_base_url=llm.get("base_url", ""),
            llm_model=llm.get("model", ""),
            llm_api_key=llm.get("api_key", ""),
            llm_timeout=llm.get("timeout", 300),
            keep_sessions=session_.get("keep_sessions", 300),
            log_keep_days=log_.get("keep_days", 7),
            req_retry_sec=int(os.environ.get("NYX_REQ_RETRY_SEC", "25")),
            daily_reflect_sec=int(os.environ.get("NYX_DAILY_REFLECT_SEC", "86400")),
            **cls._parse_compaction(raw.get("compaction", {})),
        )

    @classmethod
    def _parse_compaction(cls, raw: dict) -> dict:
        """Parse the optional ``compaction`` section of settings.json."""
        return {
            "compaction_settings": CompactionSettings(
                enabled=bool(raw.get("enabled", True)),
                reserve_tokens=int(raw.get("reserve_tokens", 16384)),
            )
        }


# Singleton — set once by boot.py before any other app module is imported.
config: Config | None = None
