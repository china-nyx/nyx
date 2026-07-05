"""Agent loop — tool-calling agent session.

Thin orchestrator: calls hooks at key points.  Compaction is implemented
entirely by hooks (sdk/hooks/compaction.py) using only the generic
``transform_context`` hook — no compaction-specific code in this file.
"""
import json
import logging
import time
from typing import Callable, Dict, List, Optional

from sdk.agent_hooks import (
    AgentHooks,
    CompositeHooks,
    HookContext,
)
from sdk.hooks.compaction import clamp_max_tokens, estimate_context_tokens
from sdk.hooks import (  # noqa: F401
    CompactionHook,
    DuplicateOutputPruner,
    RepetitiveCallGuard,
    StepLogger,
    TerminalToolHook,
)
from sdk.llm import _prune_tool_output, _strip_think
from sdk.schemas import (
    ChatMessage,
    ChatCompletionResponse,
    ResponseFormat,
)
from sdk.tools import ALL_TOOLS

logger = logging.getLogger(__name__)


class ChatClient:
    """Minimal interface required by run_agent to talk to the LLM."""
    model: str

    def chat(self, messages: list[ChatMessage], *, temperature: float,
             max_tokens: int, tools: Optional[List[Dict]] = None,
             response_format: Optional[ResponseFormat] = None) -> ChatCompletionResponse:
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
_DEFAULT_CONTEXT_WINDOW = 256_000


def _build_default_hooks(on_step, terminal_tools, compaction_settings, context_window):
    """Build the default hook chain that reproduces legacy behaviour."""
    from sdk.hooks.compaction import CompactionSettings

    parts = [RepetitiveCallGuard(), DuplicateOutputPruner()]
    if terminal_tools:
        parts.append(TerminalToolHook(terminal_tools))
    if on_step:
        parts.append(StepLogger(on_step))
    parts.append(CompactionHook(compaction_settings or CompactionSettings(), context_window=context_window))
    return CompositeHooks(*parts)


def run_agent(client: ChatClient, messages: list[ChatMessage],
              tool_executor: Callable[[str, Dict], tuple], *,
              temperature: float = 0.5,
              on_step: Optional[Callable] = None,
              tools: List[Dict] = None,
              terminal_tools: Optional[set] = None,
              response_format: Optional[ResponseFormat] = None,
              compaction_settings=None,
              context_window: int = _DEFAULT_CONTEXT_WINDOW,
              hooks: AgentHooks = None) -> Dict:
    """Tool-calling agent loop with pluggable hooks.

    Runs until the model returns a text response (no tool calls).

    All behavioural extensions — repetitive guard, duplicate pruning,
    compaction, terminal tools — are implemented as hooks via
    ``transform_context``, ``before_tool_call``, ``after_tool_call``,
    ``should_stop_after_turn``.

    Args:
        hooks: Optional AgentHooks to intercept agent loop events.
               If None, default hooks are built from on_step/terminal_tools/compaction_settings.

    Returns:
        assistant message dict with "content" key (from API or synthesised).
    """
    tools = tools or ALL_TOOLS
    _response_format = response_format
    _context_window = context_window
    msgs = list(messages)

    # Build default hooks for backward compatibility when caller passes None
    if hooks is None:
        hooks = _build_default_hooks(on_step, terminal_tools, compaction_settings, context_window)

    def _emit(event_type: str, data: Dict):
        hooks.on_event(event_type, data)

    _iteration = 0
    while True:
        _iteration += 1
        ctx = HookContext(messages=msgs, tools=tools or [], iteration=_iteration)

        # ── transform_context hook (before each LLM call) ────────────
        # Hooks can modify messages (e.g. inject compaction instruction,
        # prune old history) and override response_format for this turn.
        _active_rf = _response_format
        tcr = hooks.transform_context(msgs, _active_rf, ctx)
        if tcr:
            if tcr.messages is not None:
                msgs = tcr.messages
            if tcr.response_format is not None:
                _active_rf = tcr.response_format

        # Refresh ctx after transform (msgs may have changed)
        ctx = HookContext(messages=msgs, tools=tools or [], iteration=_iteration)

        _context_tokens = estimate_context_tokens(_msgs_to_dicts(msgs))
        _clamped_max = clamp_max_tokens(4096, _context_tokens, _context_window)

        logger.debug(f"[agent] iter {_iteration}: msgs={len(msgs)}, est_tokens={_context_tokens}, max_tokens={_clamped_max}")

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

        # ── No tool calls → exit (or continue via hook) ───────────────
        if not tcs:
            content = _strip_think(message.content or "")

            # Generic hook: allow hooks to intercept text response and continue
            new_msgs = hooks.should_continue_after_text(content, msgs, ctx)
            if new_msgs is not None:
                msgs = new_msgs
                continue

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

    logger.warning(f"[agent] exiting with error after {_iteration} iterations: no valid response from LLM")
    return _assistant_message("", stop_reason="error", error_message="no response")
