from __future__ import annotations

"""Solver — attempt-to-solve using tools and skills.

Runs an LLM session. If it modifies repo code, it commits directly.

Returns (task_output, reflection) tuple."""

from typing import Optional, Tuple

from app.config import config
from app.session import run_session

from app.prompts import get_solver_template


def solve(llm, executor, tools, requirement, tid="",
          reflect_prompt: Optional[str] = None) -> Tuple[str, Optional[str]]:
    """Returns (task_output, reflection) tuple.

    reflection is None if no post-task reflection hook was active.
    """
    task_output, reflection = run_session(llm, executor,
                      role="solver", tid=tid,
                      system_prompt=get_solver_template(requirement, tid=tid),
                      requirement=requirement,
                      tools=tools,
                      temperature=0.7,
                      prune_sessions=True, log_run=True,
                      reflect_prompt=reflect_prompt)

    if not task_output:
        raise RuntimeError("Empty response from solver")

    return task_output, reflection
