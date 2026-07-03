"""Self-heal — recover from crashes by fixing the code."""
import logging
import traceback as _tb

logger = logging.getLogger(__name__)


def run(exc: Exception) -> None:
    """Try to fix the code that caused a crash."""
    from app.config import config
    from sdk.llm import LLM
    from sdk.tools import Tools
    from app import executor, hotfixer

    requirement = (
        "## Self-Heal — Fix the following crash\n\n"
        f"### Traceback\n```\n{_tb.format_exception(exc)}\n```\n\n"
        "Find and fix the root cause so NYX can start cleanly."
    )
    llm = LLM(url=config.llm_base_url, model=config.llm_model,
                api_key=config.llm_api_key, timeout=config.llm_timeout)
    tools = Tools(cwd=config.home)
    try:
        executor.run(lambda: hotfixer.update(llm, tools.execute, requirement))
    except Exception:
        logger.exception("self-heal failed — cannot recover")
