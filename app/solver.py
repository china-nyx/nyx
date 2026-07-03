from __future__ import annotations

"""Solver — attempt-to-solve using tools and skills.

Runs an LLM session. If it modifies repo code, it commits directly.

Returns assistant text directly (no structured output)."""

from app.config import config
from app.session import run_session

from app.prompts import get_solver_template


def solve(llm, executor, tools, requirement, tid=""):
    """Returns assistant text directly."""
    out = run_session(llm, executor,
                      role="solver", tid=tid,
                      system_prompt=get_solver_template(requirement),
                      requirement=requirement,
                      tools=tools,
                      temperature=0.7,
                      prune_sessions=True, log_run=True)

    if not out:
        raise RuntimeError("Empty response from solver")

    return out
