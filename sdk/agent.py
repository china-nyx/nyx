"""Agent loop — tool-calling agent session.

Pure function: receives an LLM client and messages, returns when the model
produces text (no tool calls) or a terminal tool fires. Context compaction
is handled inline via sdk.compaction.
"""
import hashlib
import json
from collections import deque
from typing import Callable, Dict, List, Optional, Set

from core.log import get_logger

from sdk.compaction import (
    clamp_max_tokens,
    estimate_context_tokens,
    extract_file_paths,
    find_cut_point,
    format_file_note,
    should_compact,
    summarize,
)
from sdk.llm import _prune_tool_output, _strip_think
from sdk.tools import ALL_TOOLS

logger = get_logger(__name__)

# ── Repetitive call guard config ────────────────────────────────────
_REPEAT_THRESHOLD = 3       # N consecutive identical calls triggers the guard
_REPEAT_HISTORY_WINDOW = 10 # Track last N calls in the deque


def _make_args_key(tool_name: str, args: dict) -> tuple:
    """Create a hashable key from (tool_name, args) for repetitive-call detection."""
    if not isinstance(args, dict):
        args = {}
    sorted_args = json.dumps(args, sort_keys=True, default=str)
    return (tool_name, hashlib.md5(sorted_args.encode()).hexdigest())


class ChatClient:
    """Minimal interface required by run_agent to talk to the LLM.

    The LLM class in sdk.llm implements this via _post() and chat().
    """
    model: str
    _post: Callable[[Dict], Dict]


def run_agent(client: ChatClient, messages: List[Dict],
              tool_executor: Callable[[str, Dict], tuple], *,
              temperature: float = 0.5,
              on_step: Optional[Callable] = None,
              tools: List[Dict] = None,
              terminal_tools: Optional[set] = None,
              response_format: Optional[Dict] = None) -> Dict:
    """Tool-calling agent loop. tool_executor(name, args) -> (result_str, is_error).

    Runs until the model returns a text response (no tool calls).  When the message
    history grows too large for the context window, older tool exchanges are compacted
    into a summary so work can continue without losing the thread.

    Returns: {"content": str, "calls": list, "results": list}
    """
    tools = tools or ALL_TOOLS
    terminal = set(terminal_tools or [])
    _response_format = response_format
    msgs = list(messages)
    calls, results = [], []
    _initial_len = len(msgs)

    # Duplicate detection: track MD5 hashes of tool outputs
    _seen_outputs: Set[str] = set()
    _dup_count = 0
    # Compaction: track previous summary for incremental merging
    _previous_summary = ""
    # Repetitive call guard
    _repeat_history: deque = deque(maxlen=_REPEAT_HISTORY_WINDOW)
    _repeat_consecutive = 0
    _repeat_last_key = None
    _repeat_cached: Dict[str, str] = {}

    while True:
        _context_tokens = estimate_context_tokens(msgs)
        _clamped_max = clamp_max_tokens(4096, _context_tokens)

        body = {"model": client.model, "messages": msgs, "tools": tools,
                "temperature": temperature, "max_tokens": _clamped_max, "stream": False}
        if _response_format:
            body["response_format"] = _response_format
        resp = client._post(body)
        if not resp.get("choices"):
            break
        m = resp["choices"][0]["message"]
        tcs = m.get("tool_calls") or []
        if not tcs:
            return {"content": _strip_think(m.get("content") or ""), "calls": calls, "results": results}
        msgs.append(m)

        for tc in tcs:
            fn = tc.get("function", {})
            name = fn.get("name", "")
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except Exception:
                args = {}

            # ── Repetitive call guard ────────────────────────────────
            _args_key = _make_args_key(name, args)
            if _args_key == _repeat_last_key:
                _repeat_consecutive += 1
            else:
                _repeat_consecutive = 1
                _repeat_last_key = _args_key

            if _repeat_consecutive >= _REPEAT_THRESHOLD:
                _cached_result = _repeat_cached.get(_args_key, "(no cached result)")
                _warning = (
                    f"[REPETITIVE CALL GUARD] You have run this identical command "
                    f"{_repeat_consecutive} times in a row with the same result. "
                    f"Do NOT repeat it. Use the result you already have, try a DIFFERENT approach, "
                    f"or conclude with RESULT/BLOCKED."
                )
                res = _cached_result + "\n\n" + _warning
                err = False
                calls.append({"name": name, "args": args, "error": False})
                results.append(res)
                if on_step:
                    on_step(name, args, res, False)
                msgs.append({"role": "tool", "tool_call_id": tc.get("id", ""),
                             "content": _warning})
                continue

            res, err = tool_executor(name, args)
            if not err:
                _repeat_cached[_args_key] = str(res)
            _repeat_history.append(_args_key)
            calls.append({"name": name, "args": args, "error": err})
            results.append(res)
            if on_step:
                on_step(name, args, res, err)

            raw_content = f"ERROR: {res}" if err else str(res)
            out_hash = hashlib.md5(raw_content.encode(errors="replace")).hexdigest()
            if out_hash in _seen_outputs:
                _dup_count += 1
                tool_content = f"[DUPLICATE OUTPUT — same as a previous {name} call, skipping content to save tokens]"
            else:
                _seen_outputs.add(out_hash)
                tool_content = _prune_tool_output(name, raw_content[:10000])

            msgs.append({"role": "tool", "tool_call_id": tc.get("id", ""),
                         "content": tool_content})
            if name in terminal and not err:
                return {"content": str(res)[:300], "calls": calls, "results": results}

        # ── Context compaction ────────────────────────────────────────
        if should_compact(estimate_context_tokens(msgs), len(msgs)):
            _cut_idx = find_cut_point(msgs, _initial_len)
            compactable = msgs[_initial_len:_cut_idx]
            if compactable:
                _system_msg = ""
                if msgs and msgs[0].get("role") == "system":
                    _system_msg = msgs[0].get("content", "") or ""

                summary_text = summarize(client, _system_msg, compactable,
                                         previous_summary=_previous_summary)
                _previous_summary = summary_text
                logger.info(f"[agent] compaction: summarized {len(compactable)} messages -> {len(summary_text)} chars")

                file_paths = extract_file_paths(compactable)
                file_note = format_file_note(file_paths)

                dup_note = f" ({_dup_count} duplicate output(s) detected and skipped)" if _dup_count else ""
                summary_msg = {"role": "user", "content": (
                    f"[COMPACTED HISTORY — {len(compactable)} earlier tool exchanges, kept only the most recent for context{dup_note}]\n"
                    f"{summary_text}"
                    f"{file_note}\n\nContinue working on the task from where you left off."
                )}
                msgs = msgs[:_initial_len] + [summary_msg] + msgs[_cut_idx:]

    return {"content": "", "calls": calls, "results": results}
