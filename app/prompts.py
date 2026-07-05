"""Common prompt templates for solver and hotfixer."""
from app.config import config
from sdk.skills import scan_skills

# ── Shared base template ─────────────────────────────────────────

SHARED_BASE = """\
You ARE NYX — a self-evolving agent. {role_desc}

## Tools
- bash, read, write, edit
- Everything else is done via skills or bash

## Skills
- You have skills that provide specialized workflows for common tasks.
- The task below includes a <skills> block listing available skills with descriptions.
- When a skill's description matches your current task or situation:
  1. Use `read` to load the full SKILL.md at the path shown
  2. Follow its instructions exactly

## Paths
Working directory: {cwd}
Source repo: {repo}

Everything under {cwd} is your runtime workspace (read-write):
  - projects/ → long-running projects (each gets its own directory)
  - toolbox/ → shared utilities (multi-project tools only)
  - temp/ → ALL temporary and scratch files MUST go here. NEVER leave loose files anywhere else in the workspace.
  - memory/ → persistent knowledge (read INDEX.md for entry point; create/update files as needed)
  - skills/ → runtime skills (override built-in by name)
    Built-in skills are loaded from the source repo at runtime.
    Instance-specific skills go here and shadow built-in ones of the same name.
  - task/ → task state (state, priority, requirement.md, result.md, sessions/)
  - mailbox/inbox/ → incoming requirements (scheduler consumes these)"""


# ── Common builder ────────────────────────────────────────────────

def _build_prompt(role_desc: str, requirement: str, extra: str = "", tid: str = None) -> str:
    """Build system prompt with base + skills + requirement + optional extra sections."""
    skill_index = scan_skills(config.repo / "skills", config.skills_dir)
    base = SHARED_BASE.format(
        role_desc=role_desc,
        cwd=str(config.home),
        repo=str(config.repo),
    )
    tid_section = f"\n\n## Current Task ID\n{tid}" if tid else ""
    
    return base + tid_section + (f"\n\n## Available Skills\n{skill_index}\n" if skill_index else "") + f"""

## Requirement
{requirement}""" + extra


# ── Solver template ──────────────────────────────────────────────

def get_solver_template(requirement: str, tid: str = None) -> str:
    """Get solver system prompt with skills."""
    return _build_prompt(
        role_desc="Solve tasks by actually executing work with your tools.",
        requirement=requirement,
        tid=tid,
        extra="""

## Task Completion

**If you solved the task without modifying source code:**
Return a clear summary of what you did and the result. This will be written to result.md for the user.

**If you modified source code in the repo:**
Commit your changes, then return progress notes — NYX will restart with upgraded code.

## Response
Return a clear summary of what you did and the result.""",
    )


# ── Hotfixer template ────────────────────────────────────────────

def get_hotfixer_template(requirement: str) -> str:
    """Get hotfixer system prompt with skills."""
    return _build_prompt(
        role_desc="Fix the following issue by modifying source code in the repo.",
        requirement=requirement,
        extra="""

## Workflow
1. Read the relevant source files to understand the problem
2. Implement the fix using read/write/edit tools
3. Commit your changes
4. Return a summary of what you changed and why

## Response
Return a clear summary of what you did and the result.""",
    )
