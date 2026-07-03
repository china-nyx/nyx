"""Executor — run agent and restart if repo changed."""
import logging
import os
import sys
from pathlib import Path

from app.config import config
from sdk.git import Git

logger = logging.getLogger(__name__)


def _re_exec(tid: str = None, result: str = None):
    """Restart NYX via boot.py. Never returns on success."""
    if tid:
        from app import scheduler
        scheduler.mark_done(tid, result or "")
        logger.info(f"[executor] marked {tid} done before restart")

    boot_py = config.repo / "app" / "boot.py"
    logger.info("[executor] re-execing NYX...")
    os.execv(sys.executable, [sys.executable, str(boot_py)])


def run(agent_fn, tid: str = None):
    """Run an agent callable. If repo changed, commit + restart."""
    g = Git(config.repo)

    pre_head = g.short()
    logger.info(f"[executor] session start, HEAD={pre_head}")

    result = agent_fn()

    post_head = g.short()
    if post_head != pre_head:
        logger.info(f"[executor] HEAD changed ({pre_head} → {post_head}), restarting")
        _re_exec(tid, result)
    elif g.dirty():
        msg = _extract_message(result)
        g.commit(f"nyx: {msg}")
        logger.info(f"[executor] committed dirty changes, restarting")
        _re_exec(tid, result)

    return result


def _extract_message(result):
    """Extract commit message from agent result."""
    if isinstance(result, str):
        return result.strip() or "upgrade"
    return "upgrade"
