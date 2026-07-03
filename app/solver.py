from __future__ import annotations

"""Solver — attempt-to-solve using tools and skills.

Runs an LLM session. If it modifies repo code, executor detects dirty → commit + restart.

Uses merged JSON Schema mode with response_format for structured output:
{"status": "done" | "needs_upgrade", "content": "..."}

Returns assistant text from 'content' field."""

from app.config import config
from app.session import run_session
from sdk.skills import scan_skills
from sdk.schemas import ResponseFormat, JsonSchema, ToolDefinition


SYSTEM_TEMPLATE = """\
You ARE NYX — a self-evolving agent. Solve tasks by actually executing work with your tools.

## Self-Modification
You CAN and SHOULD modify NYX's own source code to solve tasks. Use write/edit tools
on files in {repo}/ to implement changes. After modifying, commit with:
`git add -A && git commit -m '<brief desc>'`

## Tools
- bash, read, write, edit
- Everything else is done via skills or bash

## Skills
- You have skills that provide specialized workflows for common tasks.
- The task below includes a <skills> block listing available skills with descriptions.
- When a skill's description matches your current task or situation:
  1. Use `read` to load the full SKILL.md at the path shown
  2. Follow its instructions exactly

## Persistence
- In-memory state is lost on restart — persist important state to disk

## Paths
Your working directory: {cwd}
Repo: {repo}

Everything under {cwd} is YOUR runtime workspace (read-write). Key subdirectories:
  - {sandbox}/ → your workspace for projects, research, data, and persistent notes
  - skills/ → runtime skills (override built-in by name)
    Built-in skills are loaded from the source repo at runtime.
    Instance-specific skills go here and shadow built-in ones of the same name.
  - task/ → task state (state, priority, requirement.md, result.md, sessions/)
  - mailbox/inbox/ → incoming requirements (scheduler consumes these)

Source code is in {repo}/ (read-write). You can modify it directly.
After modifying source code, ALWAYS commit with: `git add -A && git commit -m '<brief desc>'`.
Then return your result — NYX will detect the commit and restart automatically.

## Response
Return a clear summary of what you did and the result."""


def _build_system_prompt() -> str:
    return SYSTEM_TEMPLATE.format(
        repo=str(config.repo),
        sandbox=str(config.sandbox_dir),
        cwd=str(config.home),
    )


SOLVER_RESPONSE_SCHEMA = {
    "type": "json_schema",
    "json_schema": {
        "name": "solver_response",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "enum": ["done", "needs_upgrade"],
                    "description": (
                        "'done' if the task is fully complete. "
                        "'needs_upgrade' if NYX's own code must be changed to continue.")
                },
                "content": {
                    "type": "string",
                    "description": (
                        "If done: the full result text for the owner. "
                        "If needs_upgrade: detailed description of what capability is missing and why it is needed.")
                }
            },
            "required": ["status", "content"],
            "additionalProperties": False
        }
    }
}


def solve(llm, executor, tools, requirement, tid=""):
    """Returns assistant content from structured response."""
    skill_index = scan_skills(config.repo / "skills", config.skills_dir)
    skill_prefix = (skill_index + "\n\n" if skill_index else "")
    user = skill_prefix + f"TASK:\n{requirement}"

    response_format = ResponseFormat(
        type="json_schema",
        json_schema=JsonSchema(
            name="solver_response",
            strict=True,
            schema=SOLVER_RESPONSE_SCHEMA["json_schema"]["schema"]
        )
    )

    out = run_session(llm, executor,
                      role="solver", tid=tid,
                      system_prompt=_build_system_prompt(),
                      user_content=user,
                      tools=tools,
                      response_format=response_format,
                      temperature=0.7,
                      prune_sessions=True, log_run=True)

    if not out:
        raise RuntimeError("Empty response from solver")

    return out
