"""Centralized logging setup — configure once at boot, then use standard logging everywhere."""
import logging
import sys
from logging.handlers import TimedRotatingFileHandler

from sdk.git import Git


class _VersionFilter(logging.Filter):
    def __init__(self, repo_path):
        super().__init__()
        self._repo_path = repo_path
        self._version = None

    def filter(self, record):
        if self._version is None:
            try:
                self._version = Git(self._repo_path).short()
            except Exception:
                self._version = "???"
        record._version = self._version
        return True


def setup_logging(*, log_file, keep_days, repo_path):
    """Configure root logger with file + console handlers and version filter.

    Call once at application startup (boot.py).
    All modules then use ``logging.getLogger(__name__)`` normally.
    """
    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(_version)s [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    root = logging.getLogger()
    root.setLevel(logging.INFO)

    # Remove existing handlers to avoid duplicates on re-import/reload
    for h in list(root.handlers):
        root.removeHandler(h)

    vf = _VersionFilter(repo_path)

    from sdk.fs import ensure_dir
    from pathlib import Path
    ensure_dir(Path(log_file).parent)

    fh = TimedRotatingFileHandler(
        log_file, when="midnight", interval=1,
        backupCount=keep_days, encoding="utf-8"
    )
    fh.setFormatter(fmt)
    fh.addFilter(vf)
    root.addHandler(fh)

    sh = logging.StreamHandler(sys.stderr)
    sh.setFormatter(fmt)
    sh.addFilter(vf)
    root.addHandler(sh)
