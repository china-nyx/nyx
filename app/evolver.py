"""Evolver — worktree lifecycle: create → edit → promote → re-exec.

Orchestrates the editor in a throwaway git worktree, promotes changes to main,
and restarts NYX. Only depends on core/ modules plus app/editor.
"""
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

from core import config
from core.git import Git
from core.log import get_logger

logger = get_logger(__name__)


# ── worktree ────────────────────────────────────────────────────────

def _create_worktree() -> str:
    ts = int(time.time())
    wt = config.WORKTREES / f"upgrade-{ts}"
    config.WORKTREES.mkdir(parents=True, exist_ok=True)
    code = config.CODE
    subprocess.call(["umount", "-l", str(code)], stderr=subprocess.DEVNULL)
    git = Git(str(code))
    ref = git.short_branch() or "HEAD"
    if not git.add_worktree_detached(str(wt), ref):
        raise RuntimeError(f"failed to create worktree: {wt}")
    logger.info(f"[upgrade] worktree created: {wt} (detached)")
    return str(wt)


def _remove_worktree(wt_path: str) -> None:
    Git(str(config.CODE)).remove_worktree(wt_path)
    if Path(wt_path).exists():
        shutil.rmtree(wt_path, ignore_errors=True)


# ── promote + re-exec ──────────────────────────────────────────────

def _promote(message: str, wt_path: str, tid: Optional[str] = None) -> None:
    """Commit worktree → update main → re-exec. Never returns on success."""
    code = config.CODE
    home = config.HOME
    git = Git(str(code))

    if not git.dirty(cwd=wt_path):
        logger.info("[upgrade] no changes in worktree — nothing to promote")
        _remove_worktree(wt_path)
        _restore_parent(tid, home, message or "upgrade complete (no changes)")
        return

    msg = message or "upgrade: code change"
    git.commit_all(f"nyx: {msg}", cwd=wt_path)
    logger.info(f"[upgrade] committed: {git.rev_parse_short('HEAD', cwd=wt_path)}")

    subprocess.call(["umount", "-l", str(code)], stderr=subprocess.DEVNULL)
    time.sleep(0.1)

    branch = git.short_branch() or "HEAD"
    wt_head = git.rev_parse("HEAD", cwd=wt_path)
    git.update_ref(f"refs/heads/{branch}", wt_head)
    logger.info(f"[upgrade] updated {branch} to worktree commit")

    subprocess.check_call(["mount", "--bind", "-o", "ro", str(code), str(code)])
    _remove_worktree(wt_path)
    _restore_parent(tid, home, msg)

    boot_py = code / "core" / "boot.py"
    logger.info("[upgrade] re-execing NYX...")
    os.execv(sys.executable, [sys.executable, str(boot_py)])


def _restore_parent(tid: Optional[str], home: Path, message: str) -> None:
    """Mark upgrade task done and restore parent to running."""
    if not tid:
        return
    from app import scheduler
    scheduler.mark_done(tid, message)
    parent_file = home / "task" / tid / "parent_tid"
    if not parent_file.exists():
        return
    parent_tid = parent_file.read_text().strip()
    if not parent_tid:
        return
    scheduler.set_state(parent_tid, "running")
    nf = home / "task" / parent_tid / "note.md"
    old_note = ""
    if nf.exists():
        old_note = nf.read_text(encoding="utf-8").strip()
    child_note = f"# UPGRADE DONE\n{message}"
    combined = f"{old_note}\n\n{child_note}".strip() if old_note else child_note
    nf.write_text(combined, encoding="utf-8")
    logger.info(f"[upgrade] restored parent {parent_tid} to running")


# ── entry point ────────────────────────────────────────────────────

def run(llm, executor, *, requirement: str, tid: str = "") -> None:
    """editor → promote → re-exec. Exceptions propagate up."""
    logger.info(f"[upgrade] [tid {tid}] starting upgrade")

    wt_path = _create_worktree()
    try:
        from app import editor
        editor_result = editor.run(llm, executor, requirement, wt_path, tid=tid)
        _promote(editor_result, wt_path, tid=tid)
    finally:
        _remove_worktree(wt_path)
