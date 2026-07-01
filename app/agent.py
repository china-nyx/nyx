"""Agent — the app's main entry point.

OS process model: each requirement is a task with its own persistent directory.
The scheduler picks the next task, agent executes it (solver → evolver if needed).

    inbox/*.md → scheduler creates task/ → agent picks → solver → evolver (if needs_upgrade)
"""
import os
import signal
import sys
import time
from pathlib import Path

from core import config
from core.git import Git
from core.log import get_logger

logger = get_logger(__name__)
from sdk.llm import LLM
from sdk.tools import ALL_TOOLS, Tools
from app import solver
from app import evolver

_running = True


class _Shutdown(Exception):
    pass


def _sig(signum, frame):
    global _running
    _running = False
    logger.info(f"signal {signum}, stopping...")
    raise _Shutdown()


class Agent:
    def __init__(self, llm: LLM = None):
        self.llm = llm or LLM()
        self.ftools = Tools(cwd=config.HOME)
        self._last_try = {}  # tid -> last tick timestamp
        self.REQ_RETRY_SEC = int(os.environ.get("NYX_REQ_RETRY_SEC", "25"))
        self._last_self_reflect = 0.0
        self.SELF_REFLECT_INTERVAL = int(os.environ.get("NYX_SELF_REFLECT_SEC", "3600"))

    def _executor(self, name, args):
        return self.ftools.execute(name, args)

    # ── Self-reflection ─────────────────────────────────────────────

    def _maybe_self_reflect(self):
        """If enough time has passed since last self-reflection, create a task.

        Requirement text is read from the self-reflect SKILL.md (single source of truth).
        User can override via $NYX_HOME/config/self-reflect.md.

        Skips if there's already an active (non-done) self-reflect task waiting —
        prevents duplicate accumulation when scheduler is busy."""
        from app import scheduler
        now = time.time()
        if self.SELF_REFLECT_INTERVAL <= 0 or now - self._last_self_reflect < self.SELF_REFLECT_INTERVAL:
            return False
        # Dedup: don't create another if one is already pending/running
        for tid, info in scheduler.scan_tasks():
            src = scheduler._read(tid, "source_file") or ""
            if src == "self-reflect" and info["state"] in ("new", "running"):
                logger.info(f"[agent] skipping self-reflect — {tid} already active ({info['state']})")
                return False
        self._last_self_reflect = now
        # Allow user to override the requirement via a config file
        req_file = config.HOME / "config" / "self-reflect.md"
        if req_file.exists():
            requirement = req_file.read_text(encoding="utf-8")
        else:
            # Single source of truth: read from the self-reflect SKILL.md
            skill_file = config.CODE / "skills" / "self-reflect" / "SKILL.md"
            if not skill_file.exists():
                logger.warning("[agent] self-reflect SKILL.md not found — skipping")
                return False
            requirement = f"Priority: 10\n\n{skill_file.read_text(encoding='utf-8')}"
        tid = scheduler.create_task(requirement, priority=10, source_file="self-reflect")
        logger.info(f"[agent] auto-created self-reflection task {tid}")

    # ── Tick loop ───────────────────────────────────────────────

    def tick(self):
        """One agent tick: ingest inbox → pick task → execute."""
        from app import scheduler

        if not _running:
            return None

        # Periodic self-reflection (before inbox ingestion)
        self._maybe_self_reflect()

        # Ingest new requirements from inbox/
        scheduler.ingest_inbox()

        # Pick next task to run
        picked = scheduler.pick_next_task()
        if picked is None:
            return None

        tid, info = picked
        now = time.time()
        threshold = 0 if tid not in self._last_try else self.REQ_RETRY_SEC
        if now - self._last_try.get(tid, 0.0) >= threshold:
            self._last_try[tid] = now
            scheduler.set_current(tid)
            try:
                summary = self._execute_task(tid, info)
            finally:
                scheduler.clear_current()
            if not _running:
                return None
            if summary is not None:
                logger.info(f"[{tid}] {summary[:200]}")
            return summary
        return None

    # ── Task execution ──────────────────────────────────────────────

    def _execute_task(self, tid: str, info: dict) -> str:
        """Execute a task: run solver for new/resumed tasks, resume editor after upgrade."""

        state = info["state"]
        requirement = self._read_file(tid, "requirement.md") or ""
        note = self._read_file(tid, "note.md") or ""

        if state == "new":
            # New task — go straight to solver; it will use needs_upgrade if code changes are required
            from app import scheduler
            scheduler.set_state(tid, "running")
            return self._run_solver(tid, requirement, "")

        elif state == "running":
            resume_target = info.get("resume_target")
            if resume_target == "editor":
                # Resume editor phase after upgrade
                return self._run_editor_resume(tid, requirement, note)
            else:
                # Normal solver execution (new task or resumed from upgrade)
                return self._run_solver(tid, requirement, note)

        return f"unknown state {state}"

    def _run_solver(self, tid: str, requirement: str, note: str) -> str:
        """Run solver session for a task."""
        from app import scheduler

        r = solver.solve(self.llm, self._executor, ALL_TOOLS, requirement, note, tid=tid)

        if not r.get("result"):
            return "no result yet; will retry"

        status = r.get("status", "done")

        if status == "needs_upgrade":
            # Solver needs code changes → enter upgrade flow
            return self._run_upgrade(tid, r.get("content", ""))

        # Done — save content to result.md
        scheduler.mark_done(tid, r.get("content", ""))
        return "solved"

    def _run_upgrade(self, tid: str, content: str) -> str:
        """Enter upgrade flow: mark task as waiting, run evolver.

        content is both: this task's note for resume, and the child task's requirement.
        Child's result will be written back to parent note on completion."""
        from app import scheduler, evolver

        # content is this task's note for resume
        self._write_file(tid, "note.md", f"# WAITING FOR UPGRADE\n{content}")

        # Create child upgrade task
        child_tid = scheduler.create_task(
            requirement=content,
            priority=99,
            parent_tid=tid,
            source_file=tid,  # index.md shows parent TID as source
        )
        # Mark child as an upgrade task (editor phase)
        scheduler._write(child_tid, "resume_target", "editor")

        # Mark parent as waiting
        scheduler.set_upgrade_waiting(tid, child_tid, "solver")

        # Run evolver for the child task — it promotes and restarts (never returns on success)
        evolver.run(
            self.llm, self._executor,
            requirement=content, tid=child_tid,
        )
        # Evolver returned normally (clean worktree, no restart needed)
        return "upgrade complete (no code changes, no restart)"

    def _run_editor_resume(self, tid: str, requirement: str, note: str) -> str:
        """Resume editor after a sub-upgrade completed."""
        from app import scheduler, evolver

        logger.info(f"[{tid}] resuming editor after upgrade")
        evolver.run(
            self.llm, self._executor,
            requirement=requirement, tid=tid,
        )

    # ── File helpers ────────────────────────────────────────────────

    def _read_file(self, tid: str, name: str) -> str:
        p = config.TASK_DIR / tid / name
        if not p.exists():
            return ""
        return p.read_text(encoding="utf-8").strip()

    def _write_file(self, tid: str, name: str, content: str) -> None:
        (config.TASK_DIR / tid).mkdir(parents=True, exist_ok=True)
        (config.TASK_DIR / tid / name).write_text(content, encoding="utf-8")


# ── Main entry point ─────────────────────────────────────────────────

def run():
    """Main entry point: signal handling + agent tick loop."""
    signal.signal(signal.SIGTERM, _sig)
    signal.signal(signal.SIGINT, _sig)
    (config.HOME / "nyx.pid").write_text(str(os.getpid()))

    git = Git()
    git.ensure_repo()
    logger.info(f"NYX up (pid {os.getpid()}, version {git.short()})")

    agent = Agent(llm=LLM())

    while _running:
        try:
            if _running:
                agent.tick()
        except _Shutdown:
            pass

        sleep_n = 8
        for _ in range(sleep_n):
            if not _running:
                break
            time.sleep(1)

    logger.info("NYX stopped.")
