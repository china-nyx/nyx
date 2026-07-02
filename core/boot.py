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


def main():
    from core import config
    from sdk.git import Git

    # Ensure cwd is the runtime root so all derived paths resolve correctly
    config.ensure_runtime_dirs()
    os.chdir(str(config.HOME))

    g = Git(config.REPO)

    import importlib
    mod_name, fn_name = config.ENTRY.split(":")
    logger.info(f"version {g.short()} -> {config.ENTRY}")
    try:
        getattr(importlib.import_module(mod_name), fn_name)()
    except (KeyboardInterrupt, SystemExit):
        # Normal shutdown via SIGTERM/SIGINT — not a crash
        pass
    except Exception as e:
        logger.exception("agent crashed, starting self-heal")
        from core.self_heal import run
        run(e)


if __name__ == "__main__":
    main()
