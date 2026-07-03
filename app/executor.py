"""Executor — run agent and restart if repo changed."""
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
    """Run an agent callable. Returns (result, head_changed)."""
    g = Git(config.repo)

    pre_head = g.short()
    logger.info(f"[executor] session start, HEAD={pre_head}")

    result = agent_fn()

    post_head = g.short()
    head_changed = (post_head != pre_head)
    if head_changed:
        logger.info(f"[executor] HEAD changed ({pre_head} → {post_head})")

    return result, head_changed



