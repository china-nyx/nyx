"""Agent loop — tool-calling agent session.

Thin orchestrator: calls hooks at key points.  The caller is responsible
for building and passing an ``AgentHooks`` instance.  No default hooks
are constructed here.
"""
import json
import logging
from typing import Callable, Dict, List, Optional

from sdk.agent_hooks import (
    AfterLlmCallResult,
    AgentHooks,
    HookContext,
)

# ── Imports from other sdk modules ───────────────────────────────

from sdk.schemas import (
    AssistantMessage,
    ChatMessage,
    ChatResponseMessage,
    ResponseFormat,
)
from sdk.tools import ALL_TOOLS

logger = logging.getLogger(__name__)


# ── Tool output pruning ──────────────────────────────────────────────

def _prune_tool_output(name: str, content: str, max_chars: int = 8000) -> str:
    """Prune large tool outputs to save context tokens while preserving useful info."""
    if len(content) <= max_chars:
        return content
    half = max_chars // 2 - 100
    kept_lines_start = content[:half].count("\n")
    kept_lines_end = content[-half:].count("\n")
    skipped_lines = content.count("\n") - kept_lines_start - kept_lines_end
    truncated = (content[:half]
                 + f"\n... [{skipped_lines} lines / {len(content) - max_chars:,} chars omitted] ...\n"
                 + content[-half:])
    return truncated





# Default context window when the caller doesn't supply one.
_DEFAULT_CONTEXT_WINDOW = 256_000


def run_agent(llm, messages: list[ChatMessage],
              tool_executor: Callable[[str, Dict], tuple], *,
              model: str,
              temperature: float = 0.5,
              tools: List[Dict] = None,
              response_format: Optional[ResponseFormat] = None,
              context_window: int = _DEFAULT_CONTEXT_WINDOW,
              hooks: AgentHooks = None) -> AssistantMessage:
    """Tool-calling agent loop with pluggable hooks.

    Runs until the model returns a text response (no tool calls).

    All behavioural extensions — repetitive guard, duplicate pruning,
    compaction, terminal tools — are implemented as hooks via
    ``before_llm_call``, ``after_llm_call``, ``before_tool_call``,
    ``after_tool_call``.

    Args:
        model: Model name to pass to the LLM on each call.
        hooks: AgentHooks to intercept agent loop events.
               Built by the caller.

    Returns:
        AssistantMessage with content set to the task result.
    """

    _task_content: Optional[str] = None  # first text-only response (saved when hook continues)
    tools = tools or ALL_TOOLS
    _response_format = response_format
    _context_window = context_window
    msgs = list(messages)

    if hooks is None:
        from sdk.agent_hooks import CompositeHooks
        hooks = CompositeHooks()

    def _emit(event_type: str, data: Dict):
        hooks.on_event(event_type, data)

    _iteration = 0
    while True:
        _iteration += 1
        ctx = HookContext(messages=msgs, tools=tools or [], iteration=_iteration,
                          llm=llm)

        # ── before_llm_call hook (before each LLM call) ──────────────
        # Hooks can modify messages (e.g. compaction, pruning).
        r = hooks.before_llm_call(msgs, ctx)
        if r is not None:
            msgs = r

        # Refresh ctx after transform (msgs may have changed)
        ctx = HookContext(messages=msgs, tools=tools or [], iteration=_iteration,
                          llm=llm)

        logger.debug(f"[agent] iter {_iteration}: msgs={len(msgs)}")

        _emit("turn_start", {"iteration": _iteration})

        resp = llm.chat(
            msgs,
            model=model,
            temperature=temperature,
            max_tokens=4096,
            tools=tools if tools else None,
            response_format=_response_format,
        )

        if not resp.choices:
            logger.warning(f"[agent] empty response after {_iteration} iterations")
            break

        message = resp.choices[0].message

        # ── after_llm_call hook (after each LLM response) ────────────
        r = hooks.after_llm_call(message.model_dump(), ctx)
        if r and r.message is not None:
            message = ChatResponseMessage(**r.message)

        tool_calls = message.tool_calls or []

        # ── No tool calls → exit (or continue via hook) ──────────────
        if not tool_calls:
            content = message.content or ""

            # Check if after_llm_call requested to continue the loop
            if r and r.continue_loop:
                _task_content = content  # save task result before continuing
                msgs.append(ChatMessage(role=message.role, content=message.content))
                if r.messages_to_append:
                    msgs.extend(r.messages_to_append)
                continue

            _emit("turn_end", {"content": content})
            # Return the task result (before any hook-injected extra turn)
            return AssistantMessage(
                content=_task_content if _task_content is not None else content,
            )

        # ── Tool calls → execute with hooks ───────────────────────────
        msgs.append(ChatMessage(role=message.role, content=message.content,
                                 tool_calls=[tc.model_dump() for tc in tool_calls] if tool_calls else None))

        _terminate_batch = False
        for tc in tool_calls:
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
                return AssistantMessage(content=final_content[:300])

    logger.warning(f"[agent] exiting with error after {_iteration} iterations: no valid response from LLM")
    return AssistantMessage(content="")
