"""Common prompt templates for solver and hotfixer."""
from app.config import config

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
Your working directory: {cwd}
Repo: {repo}

Everything under {cwd} is YOUR runtime workspace (read-write). Key subdirectories:
  - {sandbox}/ → your workspace for projects, research, data, and persistent notes
  - skills/ → runtime skills (override built-in by name)
    Built-in skills are loaded from the source repo at runtime.
    Instance-specific skills go here and shadow built-in ones of the same name.
  - task/ → task state (state, priority, requirement.md, result.md, sessions/)
  - mailbox/inbox/ → incoming requirements (scheduler consumes these)

You CAN modify NYX's own source code in {repo}/ to solve tasks.

## Response
Return a clear summary of what you did and the result."""


# ── Solver template ──────────────────────────────────────────────

def get_solver_template() -> str:
    """Get solver-specific system template."""
    return SHARED_BASE.format(
        role_desc="Solve tasks by actually executing work with your tools.",
        cwd=str(config.home),
        repo=str(config.repo),
        sandbox=str(config.sandbox_dir),
    )


# ── Hotfixer template ────────────────────────────────────────────

def get_hotfixer_template(requirement: str) -> str:
    """Get hotfixer-specific system template."""
    base = SHARED_BASE.format(
        role_desc="Fix the following issue by modifying source code in the repo.",
        cwd=str(config.home),
        repo=str(config.repo),
        sandbox=str(config.sandbox_dir),
    )
    
    return base + f"""

## Requirement
{requirement}

## Workflow
1. Read the relevant source files to understand the problem
2. Implement the fix using read/write/edit tools
3. **Commit your changes**: `git add -A && git commit -m 'fix: <brief description>'`
4. Return a summary of what you changed and why

## Response
First summarize what you changed, then list changes one line per file."""


# ── Common paths (shared) ────────────────────────────────────────

def get_common_paths() -> str:
    """Get common paths string."""
    return f"""Your working directory: {config.home}
Repo: {config.repo}

Everything under {config.home} is YOUR runtime workspace (read-write). Key subdirectories:
  - {config.sandbox_dir}/ → your workspace for projects, research, data, and persistent notes
  - skills/ → runtime skills (override built-in by name)
    Built-in skills are loaded from the source repo at runtime.
    Instance-specific skills go here and shadow built-in ones of the same name.
  - task/ → task state (state, priority, requirement.md, result.md, sessions/)
  - mailbox/inbox/ → incoming requirements (scheduler consumes these)

You CAN modify NYX's own source code in {config.repo}/ to solve tasks."""
