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
            model=config.llm_model,
            api_key=config.llm_api_key,
            timeout=config.llm_timeout,
        )
        self.ftools = Tools(cwd=config.home)
        self._last_try = {}  # tid -> last tick timestamp
        self.REQ_RETRY_SEC = config.req_retry_sec


    def _executor(self, name, args):
        return self.ftools.execute(name, args)

    # ── Self-reflection ───────────────────────────────────────

    def _maybe_self_reflect(self):
        """If enough time has passed since last self-reflection, drop an inbox file."""
        from app import self_reflect
        self_reflect.maybe_drop()

    # ── Tick loop ───────────────────────────────────────

    def tick(self):
        """One agent tick: ingest inbox → pick task → execute."""
        from app import scheduler

        if not _running:
            return None

        self._maybe_self_reflect()
        scheduler.ingest_inbox()

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
            if summary is not None:
                logger.info(f"[{tid}] {summary[:200]}")
            return summary
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

        result = executor.run(
            lambda: solver.solve(self.llm, self._executor, ALL_TOOLS, requirement, tid=tid),
            on_change=on_code_change)

        if not result:
            return "no result yet; will retry"

        # No code change — mark done here
        scheduler.mark_done(tid, result)
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
