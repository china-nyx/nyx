"""Agent session helpers — shared on_step factory and session runner."""
import json
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional

from core import config
from core.git import Git
from core.log import get_logger
from sdk.fs import ensure_dir

logger = get_logger(__name__)
from sdk.tools import format_tool_log


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

    Returns: callable(name, args, res, err) matching run_agent's on_step signature.
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
                system_prompt: str, user_content: str,
                tools: List[Dict] = None, temperature: float = 0.5,
                prune_sessions: bool = False,
                log_run: bool = False) -> str:
    """Run an agent session with shared setup (session file, on_step, run_agent).

    Returns the assistant's final text content.

    Args:
        role: "solver" or "hotfixer"
        tid: task id
        system_prompt: system message content
        user_content: user message content
        tools: tool definitions (default: ALL_TOOLS)
        temperature: model temperature
        prune_sessions: if True, prune old session files beyond KEEP_SESSIONS
        log_run: if True, write a "run" record at start and "output" record at end
    """
    from sdk.agent import run_agent

    ver = Git(config.REPO).short()
    sess_dir = config.TASK_DIR / (tid or "adhoc") / "sessions"
    ensure_dir(sess_dir)
    sess_path = str(_session_file(sess_dir, role, ver))

    if prune_sessions:
        old = sorted(sess_dir.glob("*.jsonl"),
                     key=lambda p: p.stat().st_mtime, reverse=True)[config.KEEP_SESSIONS:]
        for p in old:
            p.unlink(missing_ok=True)

    _on_step = make_on_step(role, tid, sess_path=sess_path, record_fn=record_fn)

    if log_run:
        _write_session_record(sess_path, {
            "type": "run", "tid": tid,
            "requirement": user_content[:500],
            "model": getattr(llm, "model", ""),
            "cwd": str(config.HOME),
        })

    res = run_agent(llm,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
        tool_executor=executor, tools=tools,
        temperature=temperature, on_step=_on_step)

    out = (res.get("content") or "").strip()

    if log_run:
        _write_session_record(sess_path, {"type": "output", "text": out})

    return out
