"""Core config — all settings in one place.  Pure data, no side-effects on import."""
import json
import os
from pathlib import Path

from pydantic import BaseModel, Field, field_validator


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
    keep_sessions: int = 300
    log_keep_days: int = 7

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
    def sandbox_dir(self) -> Path:
        return self.home / "sandbox"

    @property
    def runtime_dirs(self) -> list:
        return [self.log_dir, self.inbox_dir, self.task_dir, self.skills_dir, self.sandbox_dir]

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

        return cls(
            repo=repo,
            home=home,
            llm_base_url=llm.get("base_url", ""),
            llm_model=llm.get("model", ""),
            llm_api_key=llm.get("api_key", ""),
            llm_timeout=llm.get("timeout", 300),
            keep_sessions=log_.get("keep_sessions", 300),
            log_keep_days=log_.get("keep_days", 7),
        )


# Singleton — set once by boot.py before any other app module is imported.
config: Config | None = None
