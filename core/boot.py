#!/usr/bin/env python3
"""NYX bootstrap — setup environment, start agent.

If agent fails to start, hotfixer is invoked to fix the code.
"""
import os
import sys
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


def _boot_self_heal(tb):
    """Boot-time crash: start hotfixer to fix the code."""
    from core import config
    from sdk.llm import LLM
    from sdk.tools import Tools
    from app import evolver, hotfixer

    requirement = (
        "## Self-Heal — Fix the following boot-time crash\n\n"
        f"### Traceback\n```\n{tb}\n```\n\n"
        "Find and fix the root cause so NYX can start cleanly."
    )
    llm = LLM()
    tools = Tools(cwd=config.HOME)
    try:
        evolver.evolve(lambda: hotfixer.fix(llm, tools.execute, requirement))
    except Exception:
        logger.exception("boot self-heal failed — cannot recover")


def main():
    from core import config
    from core.git import Git

    # Ensure cwd is the runtime root so all derived paths resolve correctly
    config.HOME.mkdir(parents=True, exist_ok=True)
    os.chdir(str(config.HOME))

    _create_agents_skills_bridge()

    g = Git()
    g.ensure_repo()
    g.cleanup_stale()

    import importlib
    mod_name, fn_name = config.ENTRY.split(":")
    logger.info(f"version {g.short()} -> {config.ENTRY}")
    try:
        getattr(importlib.import_module(mod_name), fn_name)()
    except (KeyboardInterrupt, SystemExit):
        # Normal shutdown via SIGTERM/SIGINT — not a crash
        pass
    except Exception:
        import traceback
        tb = traceback.format_exc()
        logger.exception("agent start failed — sending to evolver for self-heal")
        _boot_self_heal(tb)


if __name__ == "__main__":
    main()
