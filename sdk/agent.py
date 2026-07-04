"""Agent loop — tool-calling agent session.

Thin orchestrator: calls hooks at key points, delegates compaction logic.
All behavioural extensions live in ``sdk/hooks/<name>.py`` (one per file).
"""
import json
import logging
import os
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional

from sdk.agent_hooks import (
    AgentHooks,
    CompactionApplyResult,
    CompositeHooks,
    HookContext,
)
from sdk.hooks import (  # noqa: F401
    DefaultCompactionHook,
    DuplicateOutputPruner,
    RepetitiveCallGuard,
    StepLogger,
    TerminalToolHook,
)
from sdk.hooks.default_compaction import _default_compact_response_format  # noqa: F401
from sdk.compaction import (
    CompactionSettings,
    clamp_max_tokens,
    estimate_context_tokens,
)
from sdk.llm import _prune_tool_output, _strip_think
from sdk.schemas import (
    ChatMessage,
    ChatCompletionResponse,
)
from sdk.tools import ALL_TOOLS

logger = logging.getLogger(__name__)


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
    """Create an AssistantMessage dict (compatible with pi-ai shape)."""
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


def _build_default_hooks(on_step, terminal_tools, compaction_settings):
    """Build the default hook chain that reproduces legacy behaviour."""
    parts = [RepetitiveCallGuard(), DuplicateOutputPruner()]
    if terminal_tools:
        parts.append(TerminalToolHook(terminal_tools))
    if on_step:
        parts.append(StepLogger(on_step))
    parts.append(DefaultCompactionHook(compaction_settings or CompactionSettings()))
    return CompositeHooks(*parts)


def run_agent(client: ChatClient, messages: list[ChatMessage],
              tool_executor: Callable[[str, Dict], tuple], *,
              temperature: float = 0.5,
              on_step: Optional[Callable] = None,
              tools: List[Dict] = None,
              terminal_tools: Optional[set] = None,
              response_format: Optional[Dict] = None,
              compaction_settings: CompactionSettings = None,
              context_window: int = _DEFAULT_CONTEXT_WINDOW,
              hooks: AgentHooks = None) -> Dict:
    """Tool-calling agent loop with pluggable hooks.

    Runs until the model returns a text response (no tool calls).  When the message
    history grows too large for the context window, compaction mode is entered:
    a ``{summary: string}`` response schema is passed so the model organises its
    memory and returns a compact summary that replaces all old messages.

    Args:
        hooks: Optional AgentHooks to intercept tool calls, modify results, etc.
               If None, default hooks are built from on_step/terminal_tools
               (repetitive guard + duplicate pruning + step logging).

    Returns:
        assistant message dict with "content" key (from API or synthesised).
    """
    tools = tools or ALL_TOOLS
    _response_format = response_format
    _context_window = context_window
    msgs = list(messages)
    _initial_len = len(msgs)

    # Build default hooks for backward compatibility when caller passes None
    if hooks is None:
        hooks = _build_default_hooks(on_step, terminal_tools,
                                     compaction_settings or CompactionSettings())

    # ── Compaction state (managed by loop, hooks supply behaviour) ────
    _in_compaction_mode = False
    _compaction_response_format: Optional[Dict] = None
    _last_compaction_msg_count = 0

    def _emit(event_type: str, data: Dict):
        hooks.on_event(event_type, data)

    _iteration = 0
    while True:
        _iteration += 1
        _context_tokens = estimate_context_tokens(_msgs_to_dicts(msgs))
        _clamped_max = clamp_max_tokens(4096, _context_tokens, _context_window)

        logger.debug(f"[agent] iter {_iteration}: msgs={len(msgs)}, est_tokens={_context_tokens}, max_tokens={_clamped_max}")

        # Use compaction response_format when in compaction mode
        _active_rf = (
            _compaction_response_format if _in_compaction_mode else _response_format
        )

        _emit("turn_start", {"iteration": _iteration})

        resp = client.chat(
            msgs,
            temperature=temperature,
            max_tokens=_clamped_max,
            tools=tools if tools else None,
            response_format=_active_rf,
        )

        if not resp.choices:
            logger.warning(f"[agent] empty response after {_iteration} iterations")
            break

        message = resp.choices[0].message
        tcs = message.tool_calls or []

        # ── No tool calls → exit (or compaction mode) ────────────────
        if not tcs:
            content = _strip_think(message.content or "")

            # Compaction mode: hook parses summary and replaces history
            if _in_compaction_mode:
                apply_result = hooks.apply_compaction_summary(content, messages)
                if apply_result is None:
                    # Fallback: built-in parse (same as before for safety)
                    _summary = content
                    try:
                        _parsed = json.loads(content)
                        _summary = _parsed.get("summary", content)
                    except (json.JSONDecodeError, TypeError):
                        pass
                    if not _summary or len(_summary.strip()) < 20:
                        msgs.append(ChatMessage(
                            role="user",
                            content="Your summary is too short. Please provide a meaningful "
                                    "summary of the session's progress and call result again."))
                        continue
                    apply_result = CompactionApplyResult(
                        messages=msgs[:_initial_len] + [ChatMessage(
                            role="user",
                            content=f"[COMPACTED HISTORY]\n{_summary}\n\n"
                                    f"Continue working from where you left off.")])

                _in_compaction_mode = False
                _compaction_response_format = None
                msgs = apply_result.messages
                _last_compaction_msg_count = len(msgs)
                logger.info(
                    f"[compaction] done, msgs now={len(msgs)}"
                )
                continue

            # Normal session exit
            stop_reason = resp.choices[0].finish_reason
            _emit("turn_end", {"content": content})
            return {
                "role": "assistant",
                "content": content,
                "stopReason": stop_reason,
                "usage": {k: (resp.usage.model_dump() if resp.usage else {}).get(k, 0)
                          for k in ("input", "output", "cacheRead", "cacheWrite", "totalTokens")},
                "timestamp": int(time.time() * 1000),
            }

        # ── Tool calls → execute with hooks ───────────────────────────
        msgs.append(ChatMessage(role=message.role, content=message.content,
                                 tool_calls=[tc.model_dump() for tc in tcs] if tcs else None))

        ctx = HookContext(messages=msgs, tools=tools or [], iteration=_iteration)

        _terminate_batch = False
        for tc in tcs:
            fn = tc.function
            name = fn.name
            try:
                args = json.loads(fn.arguments or "{}")
            except Exception:
                args = {}

            # before_tool_call hook (can block execution)
            blocked = hooks.before_tool_call(name, args, ctx)
            if blocked and blocked.block:
                _emit("tool_call_blocked", {"name": name, "reason": blocked.reason})
                msgs.append(ChatMessage(role="tool", tool_call_id=tc.id, content=blocked.reason))
                continue

            # Execute tool
            res, err = tool_executor(name, args)

            # after_tool_call hook (can modify result, set terminate)
            final_content = f"ERROR: {res}" if err else str(res)
            final_err = err
            modified = hooks.after_tool_call(name, args, res, err, ctx)
            if modified:
                if modified.content is not None:
                    final_content = modified.content
                if modified.is_error is not None:
                    final_err = modified.is_error
                if modified.terminate:
                    _terminate_batch = True

            # Prune large outputs for token safety
            tool_content = _prune_tool_output(name, final_content[:10000])

            _emit("tool_call_end", {"name": name, "args": args, "error": final_err})

            msgs.append(ChatMessage(role="tool", tool_call_id=tc.id, content=tool_content))

            if _terminate_batch:
                return _assistant_message(final_content[:300])

        # should_stop_after_turn hook
        if hooks.should_stop_after_turn(msgs, ctx):
            _emit("turn_end", {"reason": "should_stop"})
            return _assistant_message("")

        # ── Context compaction trigger (hook-driven) ──────────────────
        _cur_tokens = estimate_context_tokens(_msgs_to_dicts(msgs))
        _should_compact = hooks.should_compact_hook(
            _cur_tokens, len(msgs), _context_window,
            _last_compaction_msg_count)

        if _should_compact is True and not _in_compaction_mode:
            # Ask hook for compaction instruction + response_format
            from pathlib import Path as _Path
            _memory_dir = str(_Path(os.getcwd()) / "memory")
            ci = hooks.build_compaction_instruction(_memory_dir, msgs)

            if ci is not None and ci.response_format is not None:
                _compaction_response_format = ci.response_format
            elif ci is not None:  # hook gave instruction but no custom RF
                _compaction_response_format = _default_compact_response_format()
            else:
                # Fallback: default response format
                _compaction_response_format = _default_compact_response_format()

            logger.info(f"[compaction] triggered (tokens={_cur_tokens:,}, msgs={len(msgs)})")
            _in_compaction_mode = True

            # Inject instruction message
            if ci is not None:
                msgs.append(ChatMessage(role="user", content=ci.instruction))
            else:
                # Fallback: default instruction (same as before)
                from sdk.agent_hooks import _DEFAULT_COMPACT_INSTRUCTION
                msgs.append(ChatMessage(
                    role="user",
                    content=_DEFAULT_COMPACT_INSTRUCTION.format(memory_dir=_memory_dir),
                ))
        # else: already in compaction mode, loop continues with merged schema

    logger.warning(f"[agent] exiting with error after {_iteration} iterations: no valid response from LLM")
    return _assistant_message("", stop_reason="error", error_message="no response")
