"""Agent hooks — protocol, result types, and composition.

Hook implementations live in the app layer.
This module provides the Protocol, result dataclasses, CompositeHooks, and
the shared HookContext snapshot.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Protocol, runtime_checkable

from sdk.schemas import ChatMessage

logger = logging.getLogger(__name__)


# ── Hook result types ────────────────────────────────────────────────

@dataclass(frozen=True)
class BeforeToolCallResult:
    """Return from before_tool_call to block or modify a tool call."""
    block: bool = False
    reason: str = ""


@dataclass(frozen=True)
class AfterLlmCallResult:
    """Return from after_llm_call to modify the message or control flow.

    Omitted fields (None) keep their original values.
    """
    message: Optional[Dict[str, Any]] = None       # replace message fields
    continue_loop: bool = False                     # if True, don't exit even with no tool calls
    messages_to_append: List[ChatMessage] = None   # append these before continuing


@dataclass(frozen=True)
class AfterToolCallResult:
    """Return from after_tool_call to modify the tool result.

    Omitted fields (None) keep their original values.
    """
    content: Optional[str] = None       # replace result content
    is_error: Optional[bool] = None     # replace error flag
    terminate: bool = False             # stop agent after this batch


# ── Hook context (snapshot passed to each hook) ───────────────────────

@dataclass
class HookContext:
    """Snapshot of agent state passed to hooks."""
    messages: List[ChatMessage]         # current message history
    tools: List[Dict]                   # active tool definitions
    iteration: int                      # current loop iteration
    llm: Optional[Any] = None           # LLM instance for hook-side calls (e.g. compaction)


# ── Hook Protocol (all methods optional — missing = no-op) ────────────

@runtime_checkable
class AgentHooks(Protocol):
    """Optional hooks for the agent loop. Implement any subset."""

    # ── LLM call boundary hooks ──────────────────────────────────────
    def before_llm_call(self, messages: List[ChatMessage],
                        ctx: HookContext) -> Optional[List[ChatMessage]]: ...
    def after_llm_call(self, message: Dict[str, Any],
                       ctx: HookContext) -> Optional[AfterLlmCallResult]: ...

    # ── Tool call hooks ──────────────────────────────────────────────
    def before_tool_call(self, name: str, args: Dict[str, Any],
                         ctx: HookContext) -> Optional[BeforeToolCallResult]: ...
    def after_tool_call(self, name: str, args: Dict[str, Any],
                        result: str, is_error: bool,
                        ctx: HookContext) -> Optional[AfterToolCallResult]: ...

    # ── Observer (read-only) ─────────────────────────────────────────
    def on_event(self, event_type: str, data: Dict[str, Any]): ...


# ── Composite Hooks (chain multiple hook sets) ───────────────────────

class CompositeHooks:
    """Chain multiple AgentHooks. All hooks run in registration order."""

    def __init__(self, *hook_sets: Optional[AgentHooks]):
        self._hooks = [h for h in hook_sets if h is not None]

    # ── LLM call boundary hooks ──────────────────────────────────────

    def before_llm_call(self, messages: List[ChatMessage],
                        ctx: HookContext) -> Optional[List[ChatMessage]]:
        cur = messages
        for h in self._hooks:
            r = getattr(h, 'before_llm_call', lambda *a: None)(cur, ctx)
            if r is not None:
                cur = r
        return cur if cur is not messages else None

    def after_llm_call(self, message: Dict[str, Any],
                       ctx: HookContext) -> Optional[AfterLlmCallResult]:
        combined_msg: Optional[Dict[str, Any]] = None
        continue_loop = False
        append_msgs: List[ChatMessage] = []
        for h in self._hooks:
            r = getattr(h, 'after_llm_call', lambda *a: None)(message, ctx)
            if isinstance(r, AfterLlmCallResult):
                if r.message is not None:
                    combined_msg = r.message
                if r.continue_loop:
                    continue_loop = True
                if r.messages_to_append:
                    append_msgs.extend(r.messages_to_append)
        if combined_msg is not None or continue_loop or append_msgs:
            return AfterLlmCallResult(
                message=combined_msg,
                continue_loop=continue_loop,
                messages_to_append=append_msgs or None,
            )
        return None

    # ── Tool call hooks ──────────────────────────────────────────────

    def before_tool_call(self, name: str, args: Dict[str, Any],
                         ctx: HookContext) -> Optional[BeforeToolCallResult]:
        for h in self._hooks:
            r = getattr(h, 'before_tool_call', lambda *a: None)(name, args, ctx)
            if r and r.block:
                return r
        return None

    def after_tool_call(self, name: str, args: Dict[str, Any],
                        result: str, is_error: bool,
                        ctx: HookContext) -> Optional[AfterToolCallResult]:
        content: str = result
        error: bool = is_error
        terminate = False
        for h in self._hooks:
            r = getattr(h, 'after_tool_call', lambda *a: None)(name, args, content, error, ctx)
            if r:
                if r.content is not None:
                    content = r.content
                if r.is_error is not None:
                    error = r.is_error
                if r.terminate:
                    terminate = True
        if content != result or error != is_error or terminate:
            return AfterToolCallResult(content=content, is_error=error, terminate=terminate)
        return None

    # ── Observer ─────────────────────────────────────────────────────

    def on_event(self, event_type: str, data: Dict[str, Any]) -> None:
        for h in self._hooks:
            getattr(h, 'on_event', lambda *a: None)(event_type, data)
