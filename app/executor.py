"""Executor — run agent, detect HEAD change, restart."""
import logging
import os
import sys
from pathlib import Path

from app.config import config
from sdk.git import Git

logger = logging.getLogger(__name__)


def _re_exec():
    """Restart NYX via boot.py. Never returns on success."""
    boot_py = config.repo / "app" / "boot.py"
    logger.info("[executor] re-execing NYX...")
    os.execv(sys.executable, [sys.executable, str(boot_py)])


def run(agent_fn):
    """Run an agent callable. If HEAD changed, restart (never returns).

    Returns the agent result if no code was modified.
    """
    g = Git(config.repo)
    pre_head = g.short()
    logger.info(f"[executor] session start, HEAD={pre_head}")

    result = agent_fn()

    post_head = g.short()
    if post_head != pre_head:
        logger.info(f"[executor] HEAD changed ({pre_head} → {post_head}), restarting")
        _re_exec()

    return result


def run_with_memory(agent_fn, tid: str):
    """Run agent with memory persistence.

    - Reads task/<tid>/memory.md before running (if exists)
    - Saves result to task/<tid>/memory.md if HEAD changed
    - Restarts if HEAD changed (never returns)
    - Returns result if no code was modified
    """
    mem_path = config.task_dir / tid / "memory.md"

    g = Git(config.repo)
    pre_head = g.short()
    logger.info(f"[executor] session start, HEAD={pre_head}")

    result = agent_fn()

    post_head = g.short()
    if post_head != pre_head:
        # Save memory and restart
        try:
            mem_path.write_text(result, encoding="utf-8")
            logger.info(f"[executor] saved memory to {mem_path}")
        except Exception:
            pass
        _re_exec()

    return result
