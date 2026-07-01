"""Recovery (protected, non-evolvable) — roll back to the most recent safe-boot safe point.

This is the executor for "unattended self-healing": when boot's self-check fails, it calls recover() to hard-roll-back the code to the last healthy generation.
"""
from core import config
from core.git import Git
from core.log import get_logger
logger = get_logger(__name__)


def recover() -> bool:
    """Hard-roll-back to safe-boot. Returns True on success."""
    g = Git()
    if not g.has_ref(config.SAFE_BOOT_TAG):
        return False
    g.reset_hard(config.SAFE_BOOT_TAG)
    return True


def mark_good():
    """Mark the current HEAD as the new safe recovery point (called after self-check passes)."""
    Git().tag(config.SAFE_BOOT_TAG)
