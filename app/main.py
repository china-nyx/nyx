"""Agent — the app's main entry point.

OS process model: each requirement is a task with its own persistent directory.
The scheduler picks the next task, agent executes it via evolver.evolve(solver.solve).

    inbox/*.md → scheduler creates task/ → agent picks → evolve(solver) → auto-commit+restart if dirty
"""
import os
import signal
import time
from pathlib import Path

from app.config import config
from sdk.git import Git
from app.log import get_logger

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
        """If enough time has passed since last self-reflection, drop an inbox file.

        Walks the same path as user-submitted tasks so the user can see it in inbox/.
        File name: 10-self-reflect-{YYYY-MM-DD-HH}.md (priority=10, hourly granularity)."""
        now = time.time()
        if self.SELF_REFLECT_INTERVAL <= 0 or now - self._last_self_reflect < self.SELF_REFLECT_INTERVAL:
            return False
        # Dedup: don't create another if one is already pending/running
        from app import scheduler
        for tid, info in scheduler.scan_tasks():
            src = info.get("source_file", "") or ""
            if src == "self-reflect" and info["state"] in ("new", "running"):
                logger.info(f"[agent] skipping self-reflect — {tid} already active ({info['state']})")
                return False
        self._last_self_reflect = now
        # Single source of truth: runtime SKILL.md overrides built-in (standard skill override)
        skill_file = config.SKILLS_DIR / "self-reflect" / "SKILL.md"
        if not skill_file.exists():
            skill_file = config.REPO / "skills" / "self-reflect" / "SKILL.md"
        if not skill_file.exists():
            logger.warning("[agent] self-reflect SKILL.md not found — skipping")
            return False
        requirement = skill_file.read_text(encoding='utf-8')
        stamp = time.strftime("%Y-%m-%d-%H", time.localtime())
        inbox_file = config.INBOX_DIR / f"10-self-reflect-{stamp}.md"
        inbox_file.write_text(requirement, encoding="utf-8")
        logger.info(f"[agent] dropped self-reflect inbox file {inbox_file.name}")

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
        """Execute a task via evolver.evolve(solver.solve)."""
        from app import scheduler

        requirement = scheduler.prepare_task(tid)
        if requirement is None:
            return f"unknown state {scheduler.get_state(tid)}"

        result = evolver.evolve(
            lambda: solver.solve(self.llm, self._executor, ALL_TOOLS, requirement, tid=tid),
            tid=tid)

        if not result:
            return "no result yet; will retry"

        # No code change — mark done here (evolver marks done when restarting)
        scheduler.mark_done(tid, result)
        return "solved"


# ── Main entry point ─────────────────────────────────────────────────

def run():
    """Main entry point: signal handling + agent tick loop."""
    signal.signal(signal.SIGTERM, _sig)
    signal.signal(signal.SIGINT, _sig)
    (config.HOME / "nyx.pid").write_text(str(os.getpid()))

    git = Git(config.REPO)
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
            try:
                if not _running:
                    break
                time.sleep(1)
            except _Shutdown:
                pass

    logger.info("NYX stopped.")
