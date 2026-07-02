from __future__ import annotations

"""Solver — attempt-to-solve using tools and skills.

Returns status='done' or 'needs_upgrade'. If needs_upgrade, agent calls hotfixer to fix the code.
"""

import json
import re
import time
from pathlib import Path
from typing import Dict, Optional

from core import config
from core.log import get_logger

logger = get_logger(__name__)
from sdk.tools import format_tool_log

# Skills: loaded from REPO/skills/ (built-in) and cwd/skills/ (runtime)
config.SKILLS_DIR.mkdir(parents=True, exist_ok=True)

from sdk.skills import scan_skills


def _result_brief(res, err):
    s = str(res).strip().replace("\n", " ")
    head = s[:160] + ("\u2026" if len(s) > 160 else "")
    return ("ERR: " if err else "") + head


SYSTEM_TEMPLATE = """\
You ARE NYX — a self-evolving agent. Solve tasks by actually executing work with your tools.

## Tools
- bash, read, write, edit
- Everything else is done via skills or bash

## Skills
- You have skills that provide specialized workflows for common tasks.
- Skills follow the Agent Skills standard: https://agentskills.io/specification.md
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
  - task/ → task state (note.md, result.md, sessions/)
  - mailbox/inbox/ → incoming requirements (scheduler consumes these)

Source code is in {repo}/ (read-write). You can modify it directly — if you do, NYX will restart with your changes.

## When to return needs_upgrade
Return status="needs_upgrade" when:
  - You need the hotfixer to make complex code changes (multi-file refactors, architectural changes)
  - A required tool or capability is missing and must be added to NYX
  - Your research reveals that NYX should be improved or upgraded

When returning needs_upgrade, the content field must describe:
  - What capability is missing or what code needs to change
  - Why this change is necessary
  - Which files are involved and what kind of changes are needed

## Response Format — CRITICAL
Your FINAL response (after all tool calls) MUST be a valid JSON object with exactly
these two keys. No extra text before or after the JSON.

Required format:
{{
  "status": "done",
  "content": "your result here"
}}

or:

{{
  "status": "needs_upgrade",
  "content": "detailed description of what needs to change and why"
}}

- status must be exactly "done" or "needs_upgrade" (no other values)
- content must be a non-empty string
- Wrap in ```json ...``` code fences if you prefer, but the JSON must be parseable
- Do NOT include any other JSON objects in your final response that could confuse parsing"""


def _build_system_prompt() -> str:
    return SYSTEM_TEMPLATE.format(
        repo=str(config.REPO),
        sandbox=str(config.SANDBOX_DIR),
        cwd=str(config.HOME),
    )


def _parse_solver_response(text: str) -> Optional[Dict[str, str]]:
    """Parse the solver's JSON response. Returns dict with status/content, or None."""
    text = text.strip()
    # Direct parse
    try:
        data = json.loads(text)
        if isinstance(data, dict) and "status" in data:
            return {"status": data["status"], "content": data.get("content", ""),
                    }
    except (json.JSONDecodeError, TypeError):
        pass
    # Code block
    m = re.search(r'```(?:json)?\s*\n(.*?)\n```', text, re.DOTALL)
    if m:
        try:
            data = json.loads(m.group(1))
            if isinstance(data, dict) and "status" in data:
                return {"status": data["status"], "content": data.get("content", ""),
                        }
        except (json.JSONDecodeError, TypeError):
            pass
    # Find JSON object anywhere
    brace_start = text.find('{')
    if brace_start >= 0:
        try:
            data = json.loads(text[brace_start:])
            if isinstance(data, dict) and "status" in data:
                return {"status": data["status"], "content": data.get("content", ""),
                        }
        except (json.JSONDecodeError, TypeError):
            pass
    return None


def solve(llm, executor, tools, requirement, skills_doc, tid=""):
    """Returns dict with keys: result, raw, calls, status.

    status is 'done' or 'needs_upgrade'.
    """
    _start_time = time.time()

    prior = (f"--- NYX just restarted after a code upgrade. Your changes are active. ---\nContinue from your note:\n{skills_doc}\n\n" if skills_doc else "")
    skill_index = scan_skills()
    skill_prefix = (skill_index + "\n\n" if skill_index else "")
    user = (prior + skill_prefix + f"TASK:\n{requirement}")

    # Session log per task, versioned by phase + git commit hash
    import subprocess as _sub
    _ver = _sub.run(["git", "-C", str(config.REPO), "rev-parse", "--short", "HEAD"],
                    capture_output=True, text=True).stdout.strip()[:8]
    sess_dir = config.TASK_DIR / (tid or "adhoc") / "sessions"
    sess_dir.mkdir(parents=True, exist_ok=True)
    sess = sess_dir / f"solver-{_ver}.jsonl"

    # Prune old sessions for this task (keep last N)
    _old = sorted(sess_dir.glob("*.jsonl"),
                  key=lambda p: p.stat().st_mtime, reverse=True)[config.KEEP_SESSIONS:]
    for p in _old:
        p.unlink(missing_ok=True)

    def _sess(rec):
        rec["ts"] = int(time.time())
        with open(sess, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    logger.info(f"[{tid}] solver: cwd={config.HOME}")
    _sess({"type": "run", "tid": tid, "requirement": requirement[:500],
           "model": getattr(llm, "model", ""), "cwd": str(config.HOME)})
    _step_num = 0
    _last_step_time = time.time()

    def _on_step(name, args, res_, err):
        nonlocal _step_num, _last_step_time
        _step_num += 1
        duration = round(time.time() - _last_step_time, 1)
        _last_step_time = time.time()
        logger.info(format_tool_log("solver", tid, _step_num, name, args, res_, err, duration))
        _sess({"type": "tool", "tool": name, "step": _step_num,
               "duration": duration,
               "args": args or {},
               "ok": (not err), "result": str(res_),
               "result_brief": _result_brief(res_, err)})

    system_prompt = _build_system_prompt()
    res = llm.run_agent(
        [{"role": "system", "content": system_prompt},
         {"role": "user", "content": user}],
        tool_executor=executor, tools=tools,
        temperature=0.7,
        on_step=_on_step,
    )

    out = res["content"] or ""
    _sess({"type": "output", "text": out})
    calls_list = res.get("calls", [])

    if not out.strip():
        raise RuntimeError("Empty response from llm.run_agent")

    parsed = _parse_solver_response(out)
    if parsed is None:
        # Fallback: treat raw text as done content
        num_calls = len(calls_list)
        elapsed = time.time() - _start_time
        footer = f"\n[solved with {num_calls} tool calls in {elapsed:.1f} seconds]"
        return {"result": out.strip() + footer, "raw": out,
                "content": out.strip(), "calls": calls_list, "status": "done"}

    num_calls = len(calls_list)
    elapsed = time.time() - _start_time
    footer = f"\n[solved with {num_calls} tool calls in {elapsed:.1f} seconds]"
    return {
        "result": parsed["content"] + footer,
        "raw": out,
        "content": parsed["content"],
        "calls": calls_list,
        "status": parsed["status"],
    }
