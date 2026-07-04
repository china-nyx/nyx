"""Agent loop — tool-calling agent session.

Pure function: receives an LLM client and messages, returns when the model
produces text (no tool calls). Context compaction is handled inline:
when tokens approach the window limit, a ``{summary: string}`` response
schema is injected so the model organises its memory and returns a
compact summary that replaces the old conversation history.
"""
import hashlib
import json
import logging
import os
import time
from collections import deque
from pathlib import Path
from typing import Callable, Dict, List, Optional, Set

from sdk.compaction import (
    CompactionSettings,
    clamp_max_tokens,
    estimate_context_tokens,
    should_compact,
)
from sdk.llm import _prune_tool_output, _strip_think
from sdk.schemas import (
    ChatMessage,
    ChatCompletionResponse,
    JsonSchema,
    ResponseFormat,
)
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


# ── Compaction mode instruction ─────────────────────────────────────

_COMPACT_INSTRUCTION = """\
[CONTEXT WINDOW ALERT] Your context is approaching the limit.

Please organize your working memory:
1. Read your current memory files under `{memory_dir}/` (INDEX.md, identity.md, goals/, issues/, journal/)
2. Update them with what you've learned and accomplished so far
3. When done, return a concise summary of the session's progress

After this, your conversation history will be replaced with just your summary."""


def _compact_response_format() -> ResponseFormat:
    """Build the ``{summary: string}`` schema used during compaction mode."""
    return ResponseFormat(
        type="json_schema",
        json_schema=JsonSchema(
            name="compaction_result",
            strict=True,
            schema={
                "type": "object",
                "properties": {
                    "summary": {
                        "type": "string",
                        "description": (
                            "Concise summary of session progress, decisions made, and next steps."
                        ),
                    },
                },
                "required": ["summary"],
                "additionalProperties": False,
            },
        ),
    )


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
    history grows too large for the context window, compaction mode is entered:
    a ``{summary: string}`` response schema is passed so the model organises its
    memory and returns a compact summary that replaces all old messages.

    Returns: assistant message dict with "content" key (from API or synthesized).
    """
    tools = tools or ALL_TOOLS
    terminal = set(terminal_tools or [])
    _response_format = response_format
    _compaction = compaction_settings or CompactionSettings()
    _context_window = context_window
    msgs = list(messages)
    _initial_len = len(msgs)

    # Duplicate detection: track MD5 hashes of tool outputs
    _seen_outputs: Set[str] = set()
    # Compaction: cooldown + mode tracking
    _last_compaction_msg_count = 0
    _in_compaction_mode = False
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

        # Use compaction response_format when in compaction mode
        _active_rf = (
            _compact_response_format() if _in_compaction_mode else _response_format
        )

        resp = client.chat(
            msgs,
            temperature=temperature,
            max_tokens=_clamped_max,
            tools=tools if tools else None,
            response_format=_active_rf,
        )

        if not resp.choices:
            logger.warning(f"[agent] empty response after {_iteration} iterations, returning error")
            break
        message = resp.choices[0].message
        tcs = message.tool_calls or []
        if not tcs:
            content = _strip_think(message.content or "")

            # ── Compaction mode exit via result ────────────────────
            if _in_compaction_mode:
                try:
                    _parsed = json.loads(content)
                    _summary = _parsed.get("summary", content)
                except (json.JSONDecodeError, TypeError):
                    _summary = content

                if not _summary or len(_summary.strip()) < 20:
                    msgs.append(ChatMessage(
                        role="user",
                        content="Your summary is too short. Please provide a meaningful "
                                "summary of the session's progress and call result again."))
                    continue

                _in_compaction_mode = False
                summary_msg = ChatMessage(role="user", content=(
                    f"[COMPACTED HISTORY]\n{_summary}\n\n"
                    f"Continue working from where you left off."
                ))
                msgs = msgs[:_initial_len] + [summary_msg]
                _last_compaction_msg_count = len(msgs)
                logger.info(
                    f"[compaction] done, summary={len(_summary)} chars, msgs now={len(msgs)}"
                )
                continue

            # Normal session exit
            stop_reason = resp.choices[0].finish_reason
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
                tool_content = (
                    f"[DUPLICATE OUTPUT — same as a previous {name} call, "
                    f"skipping content to save tokens]"
                )
            else:
                _seen_outputs.add(out_hash)
                tool_content = _prune_tool_output(name, raw_content[:10000])

            msgs.append(ChatMessage(role="tool", tool_call_id=tc.id, content=tool_content))
            if name in terminal and not err:
                return _assistant_message(str(res)[:300])

        # ── Context compaction trigger ───────────────────────────────
        _cur_tokens = estimate_context_tokens(_msgs_to_dicts(msgs))
        if should_compact(_cur_tokens, len(msgs),
                          _context_window, _compaction,
                          last_compaction_msg_count=_last_compaction_msg_count):
            # Determine trigger reason for logging
            _remaining = _context_window - _compaction.reserve_tokens
            _token_triggered = _cur_tokens > (_context_window - _compaction.reserve_tokens)
            _msg_triggered = len(msgs) > _compaction.compact_at
            if _token_triggered and _msg_triggered:
                _reason = (f"tokens={_cur_tokens:,}/{_remaining:,} AND "
                           f"msgs={len(msgs)}/{_compaction.compact_at}")
            elif _token_triggered:
                _reason = f"tokens={_cur_tokens:,}/{_remaining:,}"
            else:
                _reason = f"msgs={len(msgs)}/{_compaction.compact_at}"

            if not _in_compaction_mode:
                logger.info(f"[compaction] triggered ({_reason}), entering compaction mode")
                _in_compaction_mode = True

                # Determine memory dir from cwd (runtime root)
                _memory_dir = str(Path(os.getcwd()) / "memory")

                msgs.append(ChatMessage(
                    role="user",
                    content=_COMPACT_INSTRUCTION.format(memory_dir=_memory_dir),
                ))
            # else: already in compaction mode, loop continues with merged schema

    logger.warning(f"[agent] exiting with error after {_iteration} iterations: no valid response from LLM")
    return _assistant_message("", stop_reason="error", error_message="no response")
