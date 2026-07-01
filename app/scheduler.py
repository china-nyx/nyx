"""Scheduler — task lifecycle management (OS process model).

Each requirement becomes a task with its own persistent directory.
Tasks have states: new → running → done, or running → upgrade-waiting → running → done.
Done tasks are removed from the active set — scheduler only scans active tids via task/active.

    task/
    ├── active                ← active (non-done) tids, one per line
    ├── index.md              ← human-readable history (all tasks including done)
    └── <tid>/
        ├── state             ← new | running | upgrade-waiting | done
        ├── priority          ← integer (99=upgrade preemption, 50=default, 10=sched)
        ├── parent_tid        ← optional, tid of parent task (for upgrade tasks)
        ├── pending_upgrade_tid ← optional, tid of child upgrade task (when upgrade-waiting)
        ├── resume_target     ← solver | editor (which phase to resume after upgrade)
        ├── requirement.md    ← original requirement text
        ├── note.md           ← cross-restart context for solver/editor
        └── result.md         ← final output when done

Scheduling rules (priority order):
  1. Tasks whose pending_upgrade_tid child is done → restore to running
  2. Running tasks with highest priority (99 = upgrade preemption)
  3. New tasks → enter running (solver decides solve vs needs_upgrade)
"""
import os
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from core import config
from core.log import get_logger

logger = get_logger(__name__)


# ── Active task tracking ─────────────────────────────────────────────
# Only tids listed here are scanned by the scheduler.
# When a task transitions to done it is removed from this list.
# Done task directories remain on disk (for reference) but are invisible to scheduling.

_ACTIVE_FILE = "active"        # task/active — one tid per line
_CURRENT_FILE = "current_tid"   # task/current_tid — tid of the task currently being executed


def _active_tids() -> List[str]:
    """Read the set of active (non-done) task ids."""
    p = config.TASK_DIR / _ACTIVE_FILE
    if not p.exists():
        # First boot with active tracking — migrate from directory scan.
        # Only non-done tasks are added to active.
        _migrate_to_active()
        return []
    return [l.strip() for l in p.read_text(encoding="utf-8").splitlines()
            if l.strip()]


def _migrate_to_active() -> None:
    """One-time migration: scan task/ dirs and populate active file with non-done tids."""
    if not config.TASK_DIR.exists():
        return
    tids = []
    for d in sorted(config.TASK_DIR.iterdir()):
        if not d.is_dir() or d.name == "__pycache__":
            continue
        state_file = d / "state"
        if state_file.exists():
            state = state_file.read_text(encoding="utf-8").strip()
            if state != "done":
                tids.append(d.name)
    if tids:
        (config.TASK_DIR / _ACTIVE_FILE).write_text("\n".join(tids) + "\n",
                                                    encoding="utf-8")
        logger.info(f"[sched] migrated {len(tids)} active tasks to {config.TASK_DIR / _ACTIVE_FILE}")


def _add_active(tid: str) -> None:
    """Add a tid to the active set (idempotent)."""
    tids = _active_tids()
    if tid not in tids:
        tids.append(tid)
        (config.TASK_DIR / _ACTIVE_FILE).write_text("\n".join(tids) + "\n",
                                                    encoding="utf-8")


def _remove_active(tid: str) -> None:
    """Remove a tid from the active set."""
    tids = [t for t in _active_tids() if t != tid]
    (config.TASK_DIR / _ACTIVE_FILE).write_text("\n".join(tids) + "\n",
                                                encoding="utf-8")


# ── Current task tracking ─────────────────────────────────────────────
# Persists the tid of the task currently being executed.
# Used by self-heal to recover context after a crash.

def set_current(tid: str) -> None:
    """Record that tid is the task currently being executed."""
    (config.TASK_DIR / _CURRENT_FILE).write_text(tid, encoding="utf-8")


def clear_current() -> None:
    """Clear the current task marker."""
    p = config.TASK_DIR / _CURRENT_FILE
    if p.exists():
        p.unlink(missing_ok=True)


def current_tid() -> Optional[str]:
    """Return the tid of the task currently being executed, or None."""
    p = config.TASK_DIR / _CURRENT_FILE
    if not p.exists():
        return None
    return p.read_text(encoding="utf-8").strip()


# ── Task file helpers ────────────────────────────────────────────────

def _tid_file(tid: str, name: str) -> Path:
    """Path to a task's file."""
    return config.TASK_DIR / tid / name


def _read(tid: str, name: str) -> Optional[str]:
    p = _tid_file(tid, name)
    if not p.exists():
        return None
    return p.read_text(encoding="utf-8").strip()


def _write(tid: str, name: str, content: str) -> None:
    p = _tid_file(tid, name)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")


def _exists(tid: str) -> bool:
    return (config.TASK_DIR / tid).is_dir()


# ── Task creation ────────────────────────────────────────────────────

_counter = 0


def _next_tid() -> str:
    """Generate a unique task id."""
    global _counter
    _counter += 1
    return f"t{int(time.time())}-{_counter:04d}"


def create_task(requirement: str, priority: int = 50,
                parent_tid: Optional[str] = None, source_file: str = "") -> str:
    """Create a new task from a requirement. Returns tid."""
    tid = _next_tid()
    tdir = config.TASK_DIR / tid
    tdir.mkdir(parents=True, exist_ok=True)

    _write(tid, "state", "new")
    _write(tid, "priority", str(priority))
    _write(tid, "requirement.md", requirement)
    _write(tid, "note.md", "")
    if parent_tid:
        _write(tid, "parent_tid", parent_tid)
    if source_file:
        _write(tid, "source_file", source_file)

    logger.info(f"[sched] created task {tid} (pri={priority}, parent={parent_tid})")
    _add_active(tid)
    _update_index()
    return tid


# ── State transitions ────────────────────────────────────────────────

def set_state(tid: str, state: str) -> None:
    _write(tid, "state", state)
    logger.info(f"[sched] {tid} → {state}")
    _update_index()


def get_state(tid: str) -> Optional[str]:
    return _read(tid, "state")


def set_upgrade_waiting(tid: str, child_tid: str, resume_target: str) -> None:
    """Mark task as waiting for a child upgrade task to complete."""
    set_state(tid, "upgrade-waiting")
    _write(tid, "pending_upgrade_tid", child_tid)
    _write(tid, "resume_target", resume_target)


def check_resume(tid: str) -> bool:
    """Check if an upgrade-waiting task's child is done. If so, restore to running."""
    state = get_state(tid)
    if state != "upgrade-waiting":
        return False
    child_tid = _read(tid, "pending_upgrade_tid")
    if child_tid and get_state(child_tid) == "done":
        set_state(tid, "running")
        logger.info(f"[sched] {tid} resumed (child {child_tid} done)")
        return True
    return False


def mark_done(tid: str, result: str = "") -> None:
    set_state(tid, "done")
    if result:
        _write(tid, "result.md", result)
    _remove_active(tid)
    _update_index()


# ── Scheduler ────────────────────────────────────────────────────────

def scan_tasks() -> List[Tuple[str, Dict]]:
    """Scan active tasks only (not done). Done task dirs remain on disk but are invisible."""
    tasks = []
    for tid in _active_tids():
        state = get_state(tid)
        if state is None or not _exists(tid):
            continue
        priority = int(_read(tid, "priority") or 50)
        tasks.append((tid, {
            "state": state,
            "priority": priority,
            "parent_tid": _read(tid, "parent_tid"),
            "pending_upgrade_tid": _read(tid, "pending_upgrade_tid"),
            "resume_target": _read(tid, "resume_target"),
        }))
    return tasks


def pick_next_task() -> Optional[Tuple[str, Dict]]:
    """Pick the next task to run. Returns (tid, info) or None.

    Priority order:
      1. Tasks whose upgrade child is done → resume to running
      2. Running/upgrade-waiting tasks with pending_upgrade done → highest priority first
      3. New tasks → enter scheduling
    """
    tasks = scan_tasks()

    # Step 1: Resume tasks whose upgrade child completed
    for tid, info in tasks:
        if check_resume(tid):
            info["state"] = "running"

    # Step 2: Pick highest-priority running task
    running = [(tid, info) for tid, info in tasks if info["state"] == "running"]
    if running:
        running.sort(key=lambda x: -x[1]["priority"])
        return running[0]

    # Step 3: Pick highest-priority new task
    new_tasks = [(tid, info) for tid, info in tasks if info["state"] == "new"]
    if new_tasks:
        new_tasks.sort(key=lambda x: -x[1]["priority"])
        return new_tasks[0]

    return None


# ── Inbox ingestion ──────────────────────────────────────────────────

def ingest_inbox() -> List[str]:
    """Move .md files from inbox/ to task/. Returns list of new tids."""
    created = []
    if not config.INBOX_DIR.exists():
        return created
    for f in sorted(config.INBOX_DIR.glob("*.md")):
        content = f.read_text(encoding="utf-8").strip()
        if not content:
            f.unlink(missing_ok=True)
            continue

        # Extract priority from filename (e.g. 90-urgent-fix.md → 90)
        priority = 50
        try:
            prefix = f.stem.split("-", 1)[0]
            priority = int(prefix)
        except ValueError:
            pass

        tid = create_task(content, priority=priority, source_file=f.name)
        f.unlink(missing_ok=True)
        created.append(tid)
        logger.info(f"[sched] ingested {f.name} → task {tid} (pri={priority})")
    return created


# ── Human-readable index ─────────────────────────────────────────────

def _update_index() -> None:
    """Write task/index.md for human reference."""
    index_path = config.TASK_DIR / "index.md"
    lines = ["# NYX Task Index\n", "| TID | Priority | State | Source | Summary | Created |\n"]
    lines.append("|-----|----------|-------|--------|---------|----------|\n")

    if config.TASK_DIR.exists():
        for d in sorted(config.TASK_DIR.iterdir()):
            if not d.is_dir() or d.name == "__pycache__":
                continue
            tid = d.name
            state = get_state(tid) or "?"
            priority = _read(tid, "priority") or "?"
            source = _read(tid, "source_file") or "-"
            created = time.strftime("%Y-%m-%d %H:%M", time.localtime(d.stat().st_ctime))

            # Summary from requirement (first line)
            req = _read(tid, "requirement.md") or ""
            summary = req.splitlines()[0][:50] if req else ""
            result = _read(tid, "result.md") or ""
            if state == "done" and result:
                summary = result.splitlines()[0][:50]

            lines.append(f"| {tid} | {priority} | {state} | {source} | {summary} | {created} |\n")

    index_path.write_text("".join(lines), encoding="utf-8")
