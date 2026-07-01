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

    _DEFAULT_SELF_REFLECT = (
        "Priority: 10\n\n"
        "## Self-Reflection Cycle — Full Workspace Audit\n\n"
        "Use the self-reflect skill to conduct a comprehensive audit of **everything** under NYX's control.\n\n"
        "### What to Audit (6 domains)\n\n"
        "1. **Source code** (`src/`)\n"
        "   - Check for TODO/FIXME/HACK markers, dead code, missing docstrings\n"
        "   - Verify module imports work (smoke check)\n"
        "   - Cross-reference recent git history with open issues — mark resolved items\n\n"
        "2. **Documentation** (`AGENTS.md`, `README.md`)\n"
        "   - Verify file layout sections match actual directory structure\n"
        "   - Check for drift between docs and reality\n"
        "   - Look for duplicated info that could be consolidated\n\n"
        "3. **Skills** (`skills/*/SKILL.md`)\n"
        "   - Verify each skill's steps still work with current codebase\n"
        "   - Check for capability gaps — things NYX should do but has no skill for\n"
        "   - Cross-reference skill file references against actual paths\n\n"
        "4. **Sandbox contents** (`sandbox/`)\n"
        "   - Memory files: accuracy, drift, completeness; prune if too large\n"
        "   - Clean up stale scripts, data, or artifacts\n\n"
        "5. **Task system** (`task/`)\n"
        "   - Active tasks: progress assessment — moving forward or stuck?\n"
        "   - Task index: is it too large? Prune old entries?\n"
        "   - Identify looping or abandoned tasks needing intervention\n\n"
        "6. **Self-reflect itself** (meta-reflection)\n"
        "   - Is this skill's procedure optimal? What adds value, what's redundant?\n"
        "   - Are there new areas to audit not covered yet?\n"
        "   - Note improvements for the SKILL.md\n\n"
        "### Core Principle: Continuous Improvement\n\n"
        "Every cycle should leave the workspace in a slightly better state:\n"
        "- **Summarize**: Compress verbose entries. Prune resolved/obsolete content.\n"
        "- **Update**: Fix drift between docs/memory and reality.\n"
        "- **Improve**: Make skill steps clearer, scripts more robust.\n"
        "- **Discover**: Identify capability or knowledge gaps not addressed by any task.\n\n"
        "### After reflection\n"
        "- Read `sandbox/memory/INDEX.md` to find memory files, then update them\n"
        "- If code or skill changes are needed, return `needs_upgrade`\n\n"
        "This is not about solving an external task — it's about looking inward, auditing everything, and making NYX better.")

    def _maybe_self_reflect(self):
        """If enough time has passed since last self-reflection, create a task."""
        from app import scheduler
        now = time.time()
        if self.SELF_REFLECT_INTERVAL <= 0 or now - self._last_self_reflect < self.SELF_REFLECT_INTERVAL:
            return False
        self._last_self_reflect = now
        # Allow user to override the requirement via a config file
        req_file = config.HOME / "config" / "self-reflect.md"
        if req_file.exists():
            requirement = req_file.read_text(encoding="utf-8")
        else:
            requirement = self._DEFAULT_SELF_REFLECT
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
            try:
                summary = self._execute_task(tid, info)
                if not _running:
                    return None
                if summary is not None:
                    logger.info(f"[{tid}] {summary[:200]}")
                return summary
            except _Shutdown:
                raise
            except Exception as e:
                logger.exception(f"tick error for tid={tid}")
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
        try:
            evolver.run(
                self.llm, self._executor,
                requirement=content, tid=child_tid,
            )
        except Exception as e:
            logger.exception(f"[{tid}] upgrade failed for {child_tid}")
            scheduler.mark_done(child_tid, f"upgrade failed: {type(e).__name__}: {e}")
            # Restore parent to running so it can retry
            scheduler.set_state(tid, "running")
            return f"upgrade failed: {type(e).__name__}: {e}"
        # Evolver returned normally (clean worktree, no restart needed)
        return "upgrade complete (no code changes, no restart)"

    def _run_editor_resume(self, tid: str, requirement: str, note: str) -> str:
        """Resume editor after a sub-upgrade completed."""
        from app import scheduler, evolver

        logger.info(f"[{tid}] resuming editor after upgrade")
        try:
            evolver.run(
                self.llm, self._executor,
                requirement=requirement, tid=tid,
            )
        except Exception as e:
            logger.exception(f"[{tid}] editor resume failed")
            scheduler.mark_done(tid, f"editor resume failed: {e}")

    # ── File helpers ────────────────────────────────────────────────

    def _read_file(self, tid: str, name: str) -> str:
        p = config.TASK_DIR / tid / name
        if p.exists():
            try:
                return p.read_text(encoding="utf-8").strip()
            except Exception:
                pass
        return ""

    def _write_file(self, tid: str, name: str, content: str) -> None:
        p = config.TASK_DIR / tid / name
        try:
            p.write_text(content, encoding="utf-8")
        except Exception as e:
            logger.exception(f"failed to write {p}")


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
    _self_heal_count = 0
    _MAX_SELF_HEAL = 3  # max consecutive self-heals before giving up

    while _running:
        try:
            if _running:
                agent.tick()
            _self_heal_count = 0  # reset on success
        except _Shutdown:
            pass
        except Exception:
            import traceback
            tb = traceback.format_exc()
            logger.exception("unhandled exception in tick")
            _self_heal_count += 1
            if _self_heal_count > _MAX_SELF_HEAL:
                logger.error(f"self-heal limit reached ({_MAX_SELF_HEAL}), staying down")
                return
            # Spawn a self-heal upgrade task with full traceback
            from app import scheduler, evolver
            requirement = (
                "## Self-Heal — Fix the following crash\n\n"
                f"```\n{tb}\n```\n\n"
                "Find and fix the root cause. Return needs_upgrade with the exact changes needed."
            )
            child_tid = scheduler.create_task(
                requirement=requirement,
                priority=99,
                source_file="self-heal",
            )
            try:
                evolver.run(
                    agent.llm, agent._executor,
                    requirement=requirement, tid=child_tid,
                )
            except Exception:
                logger.exception(f"self-heal upgrade failed for {child_tid}")
                scheduler.mark_done(child_tid, f"self-heal failed")

        sleep_n = 8
        for _ in range(sleep_n):
            if not _running:
                break
            time.sleep(1)

    logger.info("NYX stopped.")
