"""Editor — the stable core of NYX. Runs an LLM agent session in a git worktree.

Only depends on core/ and sdk/. Can be imported from boot.py for self-heal
even when other app/ modules are broken.
"""
import json
import subprocess
from pathlib import Path

from core import config
from core.log import get_logger

logger = get_logger(__name__)

PROMPT = """\
You are NYX's upgrade editor. Read the requirement, study the source code, and implement the needed changes.

## Requirement
{requirement}

## Source Code (editable)
This is a git worktree at: {worktree}/
Read the current code here to understand what needs to change, then make the edits.

## Tools
- bash — run commands in the worktree
- read, write, edit — modify source code in the worktree

## Response
First summarize what you changed, then list changes one line per file."""


def run(llm, executor, requirement: str, worktree: str, tid: str = "") -> str:
    """Run one editor LLM session. Returns content string."""
    system_prompt = PROMPT.format(requirement=requirement, worktree=worktree)
    user_prompt = (
        f"Read the source code in the worktree at {worktree}/\n"
        f"Analyze what needs to change, implement it, and describe what you did.")

    _ver = subprocess.run(
        ["git", "-C", str(config.CODE), "rev-parse", "--short", "HEAD"],
        capture_output=True, text=True).stdout.strip()[:8]
    sess_dir = config.TASK_DIR / (tid or "adhoc") / "sessions"
    sess_dir.mkdir(parents=True, exist_ok=True)
    sess = sess_dir / f"editor-{_ver}.jsonl"

    _step_num = 0

    def _on_step(name, args, res_, err):
        nonlocal _step_num
        _step_num += 1
        logger.info(f"[editor] step {_step_num}: {name} {'✓' if not err else '✗'}")
        try:
            rec = {"type": "tool", "tool": name, "step": _step_num,
                   "args": {k: str(v)[:200] for k, v in (args or {}).items()},
                   "ok": not err, "result": str(res_)[:1000]}
            with open(sess, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        except Exception:
            pass

    from sdk.tools import ALL_TOOLS
    res = llm.run_agent(
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        tool_executor=executor, tools=ALL_TOOLS, temperature=0.5, on_step=_on_step)

    return (res.get("content") or "").strip()
