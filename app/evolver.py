"""Evolver — run an agent session, then commit + restart if repo changed."""
import os
import sys
from pathlib import Path

from core import config
from sdk.git import Git
from core.log import get_logger

logger = get_logger(__name__)


def _re_exec():
    """Restart NYX via boot.py. Never returns on success."""
    boot_py = config.REPO / "core" / "boot.py"
    logger.info("[evolver] re-execing NYX...")
    os.execv(sys.executable, [sys.executable, str(boot_py)])


def evolve(agent_fn):
    """Run an agent callable. If the repo changed (committed or dirty), commit + restart."""
    g = Git(config.REPO)

    # Record HEAD before the session
    pre_head = g.short()
    logger.info(f"[evolver] session start, HEAD={pre_head}")

    # Run the agent (solver.solve or hotfixer.fix)
    result = agent_fn()

    # Check for changes after the session
    post_head = g.short()
    if post_head != pre_head:
        logger.info(f"[evolver] HEAD changed ({pre_head} → {post_head}), restarting")
        _re_exec()
    elif g.dirty():
        msg = _extract_message(result)
        g.commit(f"nyx: {msg}")
        logger.info(f"[evolver] committed dirty changes, restarting")
        _re_exec()

    return result


def _extract_message(result):
    """Extract a commit message from agent result (full text)."""
    if isinstance(result, str):
        return result.strip() or "upgrade"
    return "upgrade"
