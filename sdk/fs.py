"""Filesystem helpers."""
import os
import tempfile
from pathlib import Path


def ensure_dir(path: Path):
    """Ensure a directory exists (parents=True, exist_ok=True)."""
    path.mkdir(parents=True, exist_ok=True)


def atomic_write_text(path: Path, content: str) -> None:
    """Write *content* (plain text) to *path* atomically."""
    ensure_dir(path.parent)
    fd, tmp = tempfile.mkstemp(suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp, str(path))
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
