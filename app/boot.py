#!/usr/bin/env python3
"""NYX bootstrap — setup environment, start agent.

If agent fails to start, hotfixer is invoked to fix the code.
"""
import logging
import os
import sys
from pathlib import Path

# Must set repo in sys.path before importing app modules
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _clean_temp(temp_dir: Path, logger=None):
    """Remove all files and subdirectories inside temp/ on restart."""
    if not temp_dir.is_dir():
        return
    for entry in temp_dir.iterdir():
        try:
            if entry.is_dir():
                import shutil
                shutil.rmtree(entry)
            else:
                entry.unlink()
        except Exception:
            if logger:
                logger.debug(f"[boot] failed to remove {entry}")
    if logger:
        logger.info("[boot] temp/ cleaned")


def main():
    repo = ROOT
    home = Path.cwd().resolve()

    # 1. Create config first (before importing any app module that reads it)
    import app.config as _cfg_mod
    from app.config import Config

    config = Config.from_settings(repo=repo, home=home)
    _cfg_mod.config = config  # set the singleton

    # 2. Ensure runtime dirs exist
    from sdk.fs import ensure_dir
    for d in config.runtime_dirs:
        ensure_dir(d)

    # 3. Clean temp/ on restart (scratch space should not persist)
    _clean_temp(config.temp_dir, logger=logging.getLogger("core.boot"))

    # 4. Setup logging
    from app.log import setup_logging
    from sdk.git import Git

    os.chdir(str(config.home))
    setup_logging(log_file=config.log_file, keep_days=config.log_keep_days,
                  repo_path=config.repo)

    logger = logging.getLogger("core.boot")
    (config.home / "nyx.pid").write_text(str(os.getpid()))

    g = Git(config.repo)
    logger.info(f"version {g.short()} (pid {os.getpid()})")

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
