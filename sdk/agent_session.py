"""Agent session helpers — shared on_step factory for solver/hotfixer."""
import time
from typing import Callable, Optional

from core.log import get_logger

logger = get_logger(__name__)
from sdk.tools import format_tool_log


def make_on_step(role: str, tid: str, sess_path: str = None,
                 record_fn: Optional[Callable] = None):
    """Create an on_step callback with shared step counter + duration tracking.

    Args:
        role: "solver" or "hotfixer" (used in log prefix)
        tid: task id
        sess_path: if set, append JSONL records to this file
        record_fn: optional fn(step_num, name, args, res, err, duration) -> dict
                   returns the record to write; if None, writes a minimal record

    Returns: callable(name, args, res, err) matching run_agent's on_step signature.
    """
    _step_num = [0]
    _last_time = [time.time()]

    def _on_step(name, args, res_, err):
        _step_num[0] += 1
        duration = round(time.time() - _last_time[0], 1)
        _last_time[0] = time.time()
        step = _step_num[0]

        logger.info(format_tool_log(role, tid, step, name, args, res_, err, duration))

        if sess_path is not None:
            try:
                rec = record_fn(step, name, args, res_, err, duration) if record_fn else {
                    "type": "tool", "tool": name, "step": step,
                    "duration": duration, "ok": not err,
                    "result": str(res_)[:1000],
                }
                rec["ts"] = int(time.time())
                with open(sess_path, "a", encoding="utf-8") as f:
                    import json
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            except Exception:
                pass

    return _on_step
