"""Hotfixer — mini code-fix agent. 4 base tools only, modifies repo source."""
import json
import subprocess
import time

from core import config
from core.log import get_logger

logger = get_logger(__name__)
from sdk.tools import format_tool_log

SYSTEM_TEMPLATE = """\
You are NYX's hotfixer. Fix the following issue by modifying source code in the repo.

## Paths
Your working directory: {cwd}
Repo: {repo}

## Tools
- bash, read, write, edit (4 base tools only)

## Requirement
{requirement}

## Response
First summarize what you changed, then list changes one line per file."""


def fix(llm, executor, requirement: str, tid: str = "") -> dict:
    """Run a hotfix LLM session. Returns dict with content key."""
    system_prompt = SYSTEM_TEMPLATE.format(
        cwd=str(config.HOME),
        repo=str(config.REPO),
        requirement=requirement,
    )

    _ver = subprocess.run(
        ["git", "-C", str(config.REPO), "rev-parse", "--short", "HEAD"],
        capture_output=True, text=True).stdout.strip()
    sess_dir = config.TASK_DIR / (tid or "adhoc") / "sessions"
    from sdk.fs import ensure_dir
    ensure_dir(sess_dir)
    sess = sess_dir / f"hotfix-{_ver}.jsonl"

    _step_num = 0
    _last_step_time = time.time()

    def _on_step(name, args, res_, err):
        nonlocal _step_num, _last_step_time
        _step_num += 1
        duration = round(time.time() - _last_step_time, 1)
        _last_step_time = time.time()
        logger.info(format_tool_log("hotfixer", tid, _step_num, name, args, res_, err, duration))
        try:
            rec = {"type": "tool", "tool": name, "step": _step_num,
                   "args": {k: str(v)[:200] for k, v in (args or {}).items()},
                   "ok": not err, "result": str(res_)[:1000]}
            with open(sess, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        except Exception:
            pass

    from sdk.agent import run_agent
    from sdk.tools import ALL_TOOLS
    res = run_agent(llm,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": (
                f"Read the source code in the repo at {config.REPO}/\n"
                f"Analyze what needs to change, implement it, and describe what you did.")},
        ],
        tool_executor=executor, tools=ALL_TOOLS, temperature=0.5, on_step=_on_step)

    return (res.get("content") or "").strip()
