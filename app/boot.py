#!/usr/bin/env python3
"""NYX bootstrap — setup environment, start agent.

If agent fails to start, hotfixer is invoked to fix the code.
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.log import get_logger
logger = get_logger("core.boot")


def main():
    from app.config import config
    from sdk.git import Git

    # Ensure cwd is the runtime root so all derived paths resolve correctly
    config.ensure_runtime_dirs()
    os.chdir(str(config.HOME))

    g = Git(config.REPO)

    logger.info(f"version {g.short()}")
    from app.main import run
    try:
        run()
    except (KeyboardInterrupt, SystemExit):
        # Normal shutdown via SIGTERM/SIGINT — not a crash
        pass
    except Exception as e:
        logger.exception("agent crashed, starting self-heal")
        from app.self_heal import run
        run(e)


if __name__ == "__main__":
    main()
