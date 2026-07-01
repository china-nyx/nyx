#!/usr/bin/env python3
"""NYX bootstrap — runs once at startup, then hands off to the agent main loop.

Its sole responsibility:
  self-check -> if healthy, mark HEAD as safe-boot and start agent;
            if unhealthy, hard-roll-back to the last safe-boot and re-exec itself (with a retry cap).
Even if code changes break the system, the next startup is caught by the self-check and auto-recovers.
"""
import atexit
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.log import get_logger
logger = get_logger("core.boot")


def _create_agents_skills_bridge():
    """Create .agents/skills/ -> skills/ symlink for cross-client discovery."""
    from core import config
    bridge = config.HOME / ".agents" / "skills"
    target = config.SKILLS_DIR
    if bridge.is_symlink():
        if bridge.resolve() == target.resolve():
            return
        bridge.unlink()
    elif bridge.exists():
        return
    try:
        bridge.parent.mkdir(parents=True, exist_ok=True)
        bridge.symlink_to(target, target_is_directory=True)
    except Exception:
        pass


def _umount_source():
    import subprocess
    try:
        subprocess.call(["umount", "-l", str(ROOT)], stderr=subprocess.DEVNULL)
    except Exception:
        pass


def _mount_source_ro():
    """Bind-mount the source repo read-only so solver cannot modify it.

    The source repo IS the code — no separate mirror needed."""
    import subprocess
    subprocess.call(["umount", "-l", str(ROOT)], stderr=subprocess.DEVNULL)
    time.sleep(0.1)
    atexit.register(_umount_source)
    subprocess.check_call(["mount", "--bind", "-o", "ro", str(ROOT), str(ROOT)])
    logger.info(f"source repo mounted read-only: {ROOT}")


def main():
    from core import config
    from core.git import Git
    from core import gate, recovery

    # Ensure cwd matches $NYX_HOME so relative paths (sandbox/, mailbox/) resolve correctly
    config.HOME.mkdir(parents=True, exist_ok=True)
    os.chdir(str(config.HOME))

    _create_agents_skills_bridge()

    # Symlink $NYX_HOME/sandbox/src -> CODE so solver sees source under sandbox/
    from core import config
    sl = config.SRC_LINK
    if sl.exists() or sl.is_symlink():
        sl.unlink()
    sl.symlink_to(ROOT)

    g = Git()
    g.ensure_repo()
    g.cleanup_stale()   # portable self-cleanup at boot: leftover candidate worktrees + uncommitted source edits from a killed generation (replaces external stop hooks)
    if not g.has_ref(config.SAFE_BOOT_TAG):
        recovery.mark_good()  # First time: set the current HEAD as the safe anchor

    ok, detail = gate.run_selfcheck()
    tries = int(os.environ.get("NYX_BOOT_TRY", "0"))

    if ok:
        os.environ.pop("NYX_BOOT_TRY", None)
        recovery.mark_good()  # Current generation healthy -> advance the safe recovery point
        _mount_source_ro()   # lock down source repo for solver
        import importlib
        mod_name, fn_name = config.ENTRY.split(":")
        logger.info(f"{detail}; version {g.short()} -> {config.ENTRY}")
        getattr(importlib.import_module(mod_name), fn_name)()
        return

    logger.error(f"UNHEALTHY: {detail}")
    if tries >= config.BOOT_MAX_RECOVER:
        logger.error("recover limit reached; staying down to avoid loop.")
        return
    if recovery.recover():
        os.environ["NYX_BOOT_TRY"] = str(tries + 1)
        logger.info(f"auto-recovered to {config.SAFE_BOOT_TAG}; re-exec.")
        os.execv(sys.executable, [sys.executable, str(ROOT / "core" / "boot.py")])
    else:
        logger.error("no safe-boot anchor; staying down.")


if __name__ == "__main__":
    main()
