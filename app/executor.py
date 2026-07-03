"""Executor — run agent, detect HEAD change, restart."""
import logging
import os
import sys
from typing import Callable, Optional

from app.config import config
from sdk.git import Git

logger = logging.getLogger(__name__)


def _re_exec():
    """Restart NYX via boot.py. Never returns on success."""
    boot_py = config.repo / "app" / "boot.py"
    logger.info("[executor] re-execing NYX...")
    os.execv(sys.executable, [sys.executable, str(boot_py)])


def run(agent_fn: Callable[[], str],
        on_change: Optional[Callable[[str], None]] = None) -> str:
    """Run an agent callable. If HEAD changed, call on_change then restart.

    Args:
        agent_fn: The agent function to run (returns result string)
        on_change: Called with the result before restart if HEAD changed.
                   If None, just restarts without side effects.

    Returns:
        Agent result if no code was modified.
    """
    g = Git(config.repo)
    pre_head = g.short()
    logger.info(f"[executor] session start, HEAD={pre_head}")

    result = agent_fn()

    post_head = g.short()
    if post_head != pre_head:
        logger.info(f"[executor] HEAD changed ({pre_head} → {post_head})")
        if on_change:
            on_change(result)
        _re_exec()

    return result
