"""Self-heal — recover from crashes by fixing the code."""
import traceback as _tb
from app.log import get_logger
logger = get_logger(__name__)


def run(exc: Exception) -> None:
    """Try to fix the code that caused a crash."""
    from app.config import config
    from sdk.llm import LLM
    from sdk.tools import Tools
    from app import evolver, hotfixer

    requirement = (
        "## Self-Heal — Fix the following crash\n\n"
        f"### Traceback\n```\n{_tb.format_exception(exc)}\n```\n\n"
        "Find and fix the root cause so NYX can start cleanly."
    )
    llm = LLM()
    tools = Tools(cwd=config.HOME)
    try:
        evolver.evolve(lambda: hotfixer.fix(llm, tools.execute, requirement))
    except Exception:
        logger.exception("self-heal failed — cannot recover")
