"""Agent session helpers — hook chain builder, step logging, and session runner."""
import json
import logging
import re
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from app.config import config
from sdk.fs import ensure_dir
from sdk.git import Git
from sdk.schemas import ChatMessage

logger = logging.getLogger(__name__)


def _tool_brief(name, args, show_full_path=False):
    """One-line brief summary of tool args for logging."""
    args = args or {}
    if name == "bash":
        return (args.get("cmd") or args.get("command") or "").strip().replace("\n", " ")[:512]
    path = args.get("path") or args.get("file") or args.get("filename")
    pat = args.get("pattern") or args.get("query")
    if path and pat:
        return f"{pat!r} in {path}"
    if path:
        return str(path)
    if pat:
        return str(pat)[:80]
    return ", ".join(f"{k}={str(v)[:30]}" for k, v in list(args.items())[:2])[:100]


def _result_summary(res, err):
    """Short result summary for inline tool-call log."""
    if not res:
        return ""
    s = str(res).strip()
    while s and s[0] in ('"', "'", "`"):
        s = s[1:]
    lines = [l.strip() for l in s.splitlines() if l.strip()]
    if not lines:
        return ""
    for line in lines:
        m = re.search(r'exit\s+(\d+)', line)
        if m:
            return f"exit {m.group(1)}"
    if err:
        return lines[0][:60]
    total_len = len(s)
    if total_len < 200:
        return lines[0]
    if len(lines) > 1:
        return f"{len(lines)} lines"
    return ""


def format_tool_log(role, context, step_num, name, args, res, err, duration, *, context2=None):
    """Format a single-line unified tool-call log entry."""
    status = "✗" if err else "✓"
    brief = _tool_brief(name, args, show_full_path=err)
    ctx = f"[{context}]"
    if context2:
        ctx += f" [{context2}]"
    parts = [f"{ctx} step {step_num}: {name} {status} ({duration:.1f}s) — {brief}"]
    summary = _result_summary(res, err)
    if summary:
        parts.append(f"→ {summary}")
    return " ".join(parts)


def _session_file(sess_dir: Path, role: str, ver: str) -> Path:
    """Path to session JSONL file."""
    return sess_dir / f"{role}-{ver}.jsonl"


def _write_session_record(sess_path: str, rec: Dict):
    """Append a JSON record to the session file."""
    rec["ts"] = int(time.time())
    with open(sess_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def make_on_step(role: str, tid: str, sess_path: str = None):
    """Create an on_step callback with shared step counter + duration tracking.

    Args:
        role: "solver" or "hotfixer" (used in log prefix)
        tid: task id
        sess_path: if set, append JSONL records to this file

    Returns: callable(name, args, res, err) used by StepLogger hook.
    """
    _step = [0]
    _last = [time.time()]

    def _on_step(name, args, res_, err):
        _step[0] += 1
        duration = round(time.time() - _last[0], 1)
        _last[0] = time.time()
        step = _step[0]

        logger.info(format_tool_log(role, tid, step, name, args, res_, err, duration))

        if sess_path is not None:
            try:
                rec = {
                    "type": "tool", "tool": name, "step": step,
                    "duration": duration, "ok": not err,
                    "result": str(res_)[:1000],
                }
                _write_session_record(sess_path, rec)
            except Exception:
                pass

    return _on_step


def run_session(llm, executor, *,
                role: str, tid: str,
                system_prompt: str, requirement: str,
                tools: List[Dict] = None, temperature: float = 0.5,
                prune_sessions: bool = False,
                log_run: bool = False,
                reflect_prompt: Optional[str] = None) -> Tuple[str, Optional[str]]:
    """Run an agent session with shared setup (session file, hooks, run_agent).

    Returns:
        (task_output, reflection) tuple. reflection is None if no reflect_prompt was given.

    Args:
        role: "solver" or "hotfixer"
        tid: task id
        system_prompt: system message content (includes skills + requirement)
        requirement: original requirement text (logged in session file)
        tools: tool definitions (default: ALL_TOOLS)
        temperature: model temperature
        prune_sessions: if True, prune old session files beyond KEEP_SESSIONS
        log_run: if True, write a "run" record at start and "output" record at end
        reflect_prompt: if set, inject one extra reflection turn after task completion
    """
    from sdk.agent import run_agent

    ver = Git(config.repo).short()
    sess_dir = config.task_dir / (tid or "adhoc") / "sessions"
    ensure_dir(sess_dir)
    sess_path = str(_session_file(sess_dir, role, ver))

    if prune_sessions:
        old = sorted(sess_dir.glob("*.jsonl"),
                     key=lambda p: p.stat().st_mtime, reverse=True)[config.keep_sessions:]
        for p in old:
            p.unlink(missing_ok=True)

    _on_step = make_on_step(role, tid, sess_path=sess_path)

    # Build hooks — app layer decides which hooks to enable
    from sdk.agent_hooks import CompositeHooks
    from app.hooks import (
        CompactionHook,
        DuplicateOutputPruner,
        PostTaskReflectHook,
        RepetitiveCallGuard,
        StepLogger,
    )
    _reflect_hook: Optional[PostTaskReflectHook] = None
    if reflect_prompt is not None:
        _reflect_hook = PostTaskReflectHook(reflect_prompt)
    _hook_list = [
        RepetitiveCallGuard(),
        DuplicateOutputPruner(),
        StepLogger(_on_step),
        CompactionHook(config.compaction_settings, context_window=config.context_window),
        _reflect_hook,
    ]
    _hooks = CompositeHooks(*_hook_list)

    _model = config.llm_model
    _n_tools = len(tools) if tools else 0
    logger.info(f"[session] {role} start tid={tid}, model={_model}, temp={temperature}, tools={_n_tools}")

    if log_run:
        _write_session_record(sess_path, {
            "type": "run", "tid": tid,
            "requirement": requirement[:500],
            "model": _model,
            "cwd": str(config.home),
        })

    result = run_agent(llm,
        messages=[ChatMessage(role="system", content=system_prompt)],
        tool_executor=executor, tools=tools,
        model=config.llm_model,
        temperature=temperature,
        hooks=_hooks)

    task_output = (result.content or "").strip()
    reflection = None
    if _reflect_hook and _reflect_hook.reflection:
        reflection = _reflect_hook.reflection.strip() or None

    # Print thought on a separate line if present
    if task_output:
        logger.info(f"[thought] {task_output}")

    logger.info(f"[session] {role} end tid={tid}, output={len(task_output)} chars")

    if log_run:
        _write_session_record(sess_path, {"type": "output", "text": task_output})

    return task_output, reflection
