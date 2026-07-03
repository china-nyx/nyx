"""Hotfixer — mini code-fix agent. 4 base tools only, modifies repo source."""
from app.config import config
from app.session import run_session

SYSTEM_TEMPLATE = """\
You are NYX's hotfixer. Fix the following issue by modifying source code in the repo.

## Paths
Your working directory: {cwd}
Repo: {repo}

## Tools
- bash, read, write, edit (4 base tools only)

## Requirement
{requirement}

## Workflow
1. Read the relevant source files to understand the problem
2. Implement the fix using read/write/edit tools
3. **Commit your changes**: `git add -A && git commit -m 'fix: <brief description>'`
4. Return a summary of what you changed and why

## Response
First summarize what you changed, then list changes one line per file."""


def fix(llm, executor, requirement: str, tid: str = "") -> str:
    """Run a hotfix LLM session. Returns assistant text (for executor → commit message)."""
    system_prompt = SYSTEM_TEMPLATE.format(
        cwd=str(config.home),
        repo=str(config.repo),
        requirement=requirement,
    )

    return run_session(llm, executor,
                       role="hotfixer", tid=tid,
                       system_prompt=system_prompt,
                       user_content=(
                           f"Read the source code in the repo at {config.repo}/\n"
                           f"Analyze what needs to change, implement it, and describe what you did."),
                       temperature=0.5)
