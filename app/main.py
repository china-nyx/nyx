"""Agent — the app's main entry point.

OS process model: each requirement is a task with its own persistent directory.
The scheduler picks the next task, agent executes it via executor.run().

    inbox/*.md → scheduler creates task/ → agent picks → executor.run(solver) → restart if HEAD changed
"""
import logging
import os
import signal
import time
from pathlib import Path
from typing import Optional

from app.config import config
from sdk.git import Git
from sdk.llm import LLM
from sdk.tools import ALL_TOOLS, Tools
from app import solver

logger = logging.getLogger(__name__)

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
        self.llm = llm or LLM(
            url=config.llm_base_url,
            api_key=config.llm_api_key,
            timeout=config.llm_timeout,
        )
        self.ftools = Tools(cwd=config.home)
        self._last_try = {}  # tid -> last tick timestamp
        self.REQ_RETRY_SEC = config.req_retry_sec


    def _executor(self, name, args):
        return self.ftools.execute(name, args)

    # ── Daily reflection ───────────────────────────────────────

    def _maybe_daily_reflect(self):
        """If enough time has passed since last daily reflection, drop an inbox file."""
        from app import daily_reflect
        daily_reflect.maybe_drop()

    # ── Post-task reflection ────────────────────────────────────────

    def _build_reflection_prompt(self) -> Optional[str]:
        """Build reflection prompt — tell the model to read task-reflect skill."""
        skill_file = config.skills_dir / "task-reflect" / "SKILL.md"
        if not skill_file.exists():
            skill_file = config.repo / "skills" / "task-reflect" / "SKILL.md"
        if not skill_file.exists():
            return None
        return f"Read the task-reflect skill at {skill_file} and follow its instructions."

    def _save_reflection(self, tid: str, reflection: str):
        """Save post-task reflection to task directory."""
        try:
            from sdk.fs import ensure_dir
            task_dir = config.task_dir / tid
            ensure_dir(task_dir)
            (task_dir / "reflection.md").write_text(reflection, encoding="utf-8")
            logger.info(f"[{tid}] saved reflection ({len(reflection)} chars)")
        except Exception:
            logger.exception(f"[{tid}] failed to save reflection")

    # ── Tick loop ───────────────────────────────────────

    def tick(self):
        """One agent tick: ingest inbox → pick task → execute."""
        from app import scheduler

        if not _running:
            return None

        self._maybe_daily_reflect()
        ingested = scheduler.ingest_inbox()

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
                summary = self._execute_task(tid)
            finally:
                scheduler.clear_current()
            if not _running:
                return None
            return summary
        else:
            wait_left = round(threshold - (now - self._last_try.get(tid, 0.0)), 1)
            logger.debug(f"[tick] {tid} retry cooldown, waiting {wait_left}s")
        return None

    def _execute_task(self, tid: str) -> str:
        """Execute a task via executor.run(solver.solve)."""
        from app import scheduler, executor

        requirement = scheduler.prepare_task(tid)
        if requirement is None:
            return f"unknown state {scheduler.get_state(tid)}"

        # Read memory from previous upgrade session (if exists)
        prev_memory = scheduler._read(tid, "memory.md")
        if prev_memory:
            requirement = f"{requirement}\n\n## Previous Session Memory\n{prev_memory}"

        def on_code_change(result: str):
            """Save memory when code was modified."""
            try:
                scheduler._write(tid, "memory.md", result)
                logger.info(f"[{tid}] saved memory")
            except Exception:
                pass

        reflect_prompt = self._build_reflection_prompt()
        task_output, reflection = executor.run(
            lambda: solver.solve(self.llm, self._executor, ALL_TOOLS, requirement,
                                 tid=tid, reflect_prompt=reflect_prompt),
            on_change=on_code_change)

        if not task_output:
            return "no result yet; will retry"

        # No code change — mark done here
        scheduler.mark_done(tid, task_output)

        # Save reflection output (if post-task reflection hook ran)
        if reflection:
            self._save_reflection(tid, reflection)

        return "solved"


# ── Main entry point ─────────────────────────────────────────────────

def run():
    """Main entry point: signal handling + agent tick loop."""
    signal.signal(signal.SIGTERM, _sig)
    signal.signal(signal.SIGINT, _sig)

    git = Git(config.repo)
    logger.info(f"NYX up (pid {os.getpid()}, version {git.short()})")

    agent = Agent()

    while _running:
        try:
            if _running:
                agent.tick()
            time.sleep(1)  # idle poll interval — prevents 100% CPU spin
        except _Shutdown:
            pass
        except Exception as exc:
            logger.exception("agent crashed, starting self-heal")
            from app.self_heal import run as self_heal_run
            self_heal_run(exc)
