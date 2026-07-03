"""Self-reflection — periodic self-audit task generation."""
import logging
import time
from pathlib import Path

from app.config import config

logger = logging.getLogger(__name__)


def _load_timestamp() -> float:
    """Load last self-reflection timestamp from disk."""
    p = config.task_dir / ".last_self_reflect"
    if p.exists():
        try:
            return float(p.read_text(encoding="utf-8"))
        except Exception:
            pass
    return 0.0


def _save_timestamp(ts: float):
    """Save last self-reflection timestamp to disk."""
    p = config.task_dir / ".last_self_reflect"
    try:
        p.write_text(str(ts), encoding="utf-8")
    except Exception:
        pass




_last: float = _load_timestamp()


def maybe_drop() -> bool:
    """Drop a self-reflect inbox file if enough time has passed.

    Returns:
        True if a file was dropped, False otherwise
    """
    global _last

    from app.config import config
    if config.self_reflect_sec <= 0:
        return False

    now = time.time()
    from app.config import config
    if now - _last < config.self_reflect_sec:
        return False

    # Dedup: skip if self-reflect task is already pending/running
    from app import scheduler
    for tid, info in scheduler.scan_tasks():
        src = info.get("source_file", "") or ""
        if src == "self-reflect" and info["state"] in ("new", "running"):
            logger.info(f"[self-reflect] skipping — {tid} already active ({info['state']})")
            return False

    # Load SKILL.md
    skill_file = config.skills_dir / "self-reflect" / "SKILL.md"
    if not skill_file.exists():
        skill_file = config.repo / "skills" / "self-reflect" / "SKILL.md"
    if not skill_file.exists():
        logger.warning("[self-reflect] SKILL.md not found — skipping")
        return False

    requirement = skill_file.read_text(encoding="utf-8")
    stamp = time.strftime("%Y-%m-%d-%H", time.localtime())
    inbox_file = config.inbox_dir / f"10-self-reflect-{stamp}.md"
    inbox_file.write_text(requirement, encoding="utf-8")

    _last = now
    _save_timestamp(_last)

    logger.info(f"[self-reflect] dropped inbox file {inbox_file.name}")
    return True
