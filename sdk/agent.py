"""Agent loop — tool-calling agent session.

Pure function: receives an LLM client and messages, returns when the model
produces text (no tool calls) or a terminal tool fires. Context compaction
is handled inline via sdk.compaction.
"""
import hashlib
import json
import logging
import time
from collections import deque
from typing import Callable, Dict, List, Optional, Set

from sdk.compaction import (
    CompactionSettings,
    clamp_max_tokens,
    estimate_context_tokens,
    extract_file_paths,
    find_cut_point,
    format_file_note,
    should_compact,
    summarize,
)
from sdk.llm import _prune_tool_output, _strip_think
from sdk.schemas import ChatMessage, ChatCompletionResponse
from sdk.tools import ALL_TOOLS

logger = logging.getLogger(__name__)

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

    Implemented by sdk.llm.LLM.  The chat() method must accept the same
    keyword arguments that run_agent passes: temperature, max_tokens,
    tools (list of tool definition dicts), and response_format.
    """
    model: str

    def chat(self, messages: list[ChatMessage], *, temperature: float,
             max_tokens: int, tools: Optional[List[Dict]] = None,
             response_format: Optional[Dict] = None) -> ChatCompletionResponse:
        ...


def _assistant_message(content: str, *, stop_reason: str = "stop",
                       error_message: str = None) -> Dict:
    """Create an AssistantMessage dict (compatible with pi-ai shape).

    Fields: role, content, stopReason, usage, timestamp.
    error_message is set only when stop_reason is 'error'.
    """
    msg = {
        "role": "assistant",
        "content": content,
        "stopReason": stop_reason,
        "usage": {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0, "totalTokens": 0},
        "timestamp": int(time.time() * 1000),
    }
    if error_message:
        msg["errorMessage"] = error_message
    return msg


def _msgs_to_dicts(msgs: list[ChatMessage]) -> list[dict]:
    """Convert ChatMessage list to raw dict list for internal functions."""
    return [m.model_dump(exclude_none=True) for m in msgs]


# Default context window when the caller doesn't supply one.
_DEFAULT_CONTEXT_WINDOW = 128_000


def run_agent(client: ChatClient, messages: list[ChatMessage],
              tool_executor: Callable[[str, Dict], tuple], *,
              temperature: float = 0.5,
              on_step: Optional[Callable] = None,
              tools: List[Dict] = None,
              terminal_tools: Optional[set] = None,
              response_format: Optional[Dict] = None,
              compaction_settings: CompactionSettings = None,
              context_window: int = _DEFAULT_CONTEXT_WINDOW) -> Dict:
    """Tool-calling agent loop. tool_executor(name, args) -> (result_str, is_error).

    Runs until the model returns a text response (no tool calls).  When the message
    history grows too large for the context window, older tool exchanges are compacted
    into a summary so work can continue without losing the thread.

    Returns: assistant message dict with "content" key (from API or synthesized).
    """
    tools = tools or ALL_TOOLS
    terminal = set(terminal_tools or [])
    _response_format = response_format
    _compaction = compaction_settings or CompactionSettings()
    _context_window = context_window
    msgs = list(messages)
    _initial_len = len(msgs)

    logger.info(f"[agent] start: {_initial_len} initial msgs, model={getattr(client, 'model', '?')}, temp={temperature}")

    # Duplicate detection: track MD5 hashes of tool outputs
    _seen_outputs: Set[str] = set()
    _dup_count = 0
    # Compaction: track previous summary for incremental merging + cooldown
    _previous_summary = ""
    _last_compaction_msg_count = 0  # msg_count at last compaction (for cooldown)
    # Repetitive call guard
    _repeat_history: deque = deque(maxlen=_REPEAT_HISTORY_WINDOW)
    _repeat_consecutive = 0
    _repeat_last_key = None
    _repeat_cached: Dict[str, str] = {}

    _iteration = 0
    while True:
        _iteration += 1
        _context_tokens = estimate_context_tokens(_msgs_to_dicts(msgs))
        _clamped_max = clamp_max_tokens(4096, _context_tokens, _context_window)

        logger.debug(f"[agent] iter {_iteration}: msgs={len(msgs)}, est_tokens={_context_tokens}, max_tokens={_clamped_max}")

        resp = client.chat(
            msgs,
            temperature=temperature,
            max_tokens=_clamped_max,
            tools=tools if tools else None,
            response_format=_response_format,
        )

        if not resp.choices:
            logger.warning(f"[agent] empty response after {_iteration} iterations, returning error")
            break
        message = resp.choices[0].message
        tcs = message.tool_calls or []
        if not tcs:
            content = _strip_think(message.content or "")
            stop_reason = resp.choices[0].finish_reason
            logger.info(f"[agent] done after {_iteration} iterations, output={len(content)} chars")
            return {
                "role": "assistant",
                "content": content,
                "stopReason": stop_reason,
                "usage": {k: (resp.usage.model_dump() if resp.usage else {}).get(k, 0)
                          for k in ("input", "output", "cacheRead", "cacheWrite", "totalTokens")},
                "timestamp": int(time.time() * 1000),
            }
        msgs.append(ChatMessage(role=message.role, content=message.content,
                                 tool_calls=[tc.model_dump() for tc in tcs] if tcs else None))

        for tc in tcs:
            fn = tc.function
            name = fn.name
            try:
                args = json.loads(fn.arguments or "{}")
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
                logger.warning(f"[agent] repetitive call guard: {name} called {_repeat_consecutive}x consecutively")
                _cached_result = _repeat_cached.get(_args_key, "(no cached result)")
                _warning = (
                    f"[REPETITIVE CALL GUARD] You have run this identical command "
                    f"{_repeat_consecutive} times in a row with the same result. "
                    f"Do NOT repeat it. Use the result you already have, try a DIFFERENT approach, "
                    f"or conclude with RESULT/BLOCKED."
                )
                res = _cached_result + "\n\n" + _warning
                err = False
                if on_step:
                    on_step(name, args, res, False)
                msgs.append(ChatMessage(role="tool", tool_call_id=tc.id, content=_warning))
                continue

            res, err = tool_executor(name, args)
            if not err:
                _repeat_cached[_args_key] = str(res)
            _repeat_history.append(_args_key)
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

            msgs.append(ChatMessage(role="tool", tool_call_id=tc.id, content=tool_content))
            if name in terminal and not err:
                return _assistant_message(str(res)[:300])

        # ── Context compaction ────────────────────────────────────────
        _cur_tokens = estimate_context_tokens(_msgs_to_dicts(msgs))
        _remaining = _context_window - _compaction.reserve_tokens
        if should_compact(_cur_tokens, len(msgs),
                          _context_window, _compaction,
                          last_compaction_msg_count=_last_compaction_msg_count):
            # Determine trigger reason for logging
            _token_triggered = _cur_tokens > (_context_window - _compaction.reserve_tokens)
            _msg_triggered = len(msgs) > _compaction.compact_at
            if _token_triggered and _msg_triggered:
                _reason = f"tokens={_cur_tokens:,}/{_remaining:,} AND msgs={len(msgs)}/{_compaction.compact_at}"
            elif _token_triggered:
                _reason = f"tokens={_cur_tokens:,}/{_remaining:,}"
            else:
                _reason = f"msgs={len(msgs)}/{_compaction.compact_at}"

            _cut_idx = find_cut_point(_msgs_to_dicts(msgs), _initial_len,
                                      _compaction.keep_recent_tokens)
            compactable = msgs[_initial_len:_cut_idx]
            if compactable:
                logger.info(f"[compaction] triggered ({_reason}), cutting {len(compactable)} msgs (keep from idx {_cut_idx})")

                _system_msg = ""
                if msgs and msgs[0].role == "system":
                    _system_msg = msgs[0].content or ""

                summary_text = summarize(client, _system_msg, _msgs_to_dicts(compactable),
                                         _compaction,
                                         previous_summary=_previous_summary)
                _previous_summary = summary_text
                logger.info(f"[compaction] summarized {len(compactable)} messages -> {len(summary_text)} chars")

                file_paths = extract_file_paths(_msgs_to_dicts(compactable))
                file_note = format_file_note(file_paths)

                dup_note = f" ({_dup_count} duplicate output(s) detected and skipped)" if _dup_count else ""
                summary_msg = ChatMessage(role="user", content=(
                    f"[COMPACTED HISTORY — {len(compactable)} earlier tool exchanges, kept only the most recent for context{dup_note}]\n"
                    f"{summary_text}"
                    f"{file_note}\n\nContinue working on the task from where you left off."
                ))
                msgs = msgs[:_initial_len] + [summary_msg] + msgs[_cut_idx:]
                # Record post-compaction msg count for cooldown tracking
                _last_compaction_msg_count = len(msgs)

    logger.warning(f"[agent] exiting with error after {_iteration} iterations: no valid response from LLM")
    return _assistant_message("", stop_reason="error", error_message="no response")
