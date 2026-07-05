"""Agent hooks — protocol, result types, and composition.

All hook *implementations* live in ``sdk/hooks/<name>.py`` (one per file).
This module provides the Protocol, result dataclasses, CompositeHooks, and
the shared HookContext snapshot.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Protocol, runtime_checkable

from sdk.schemas import ChatMessage, ResponseFormat

logger = logging.getLogger(__name__)


# ── Hook result types ────────────────────────────────────────────────

@dataclass(frozen=True)
class BeforeToolCallResult:
    """Return from before_tool_call to block or modify a tool call."""
    block: bool = False
    reason: str = ""


@dataclass(frozen=True)
class AfterToolCallResult:
    """Return from after_tool_call to modify the tool result.

    Omitted fields (None) keep their original values.
    """
    content: Optional[str] = None       # replace result content
    is_error: Optional[bool] = None     # replace error flag
    terminate: bool = False             # stop agent after this batch


@dataclass
class TransformContextResult:
    """Return from transform_context to modify messages/response_format before the LLM call.

    Omitted fields keep their current values.
    """
    messages: Optional[List[ChatMessage]] = None   # replace message history
    response_format: Optional[ResponseFormat] = None  # override response_format for this turn


# ── Hook context (snapshot passed to each hook) ───────────────────────

@dataclass
class HookContext:
    """Snapshot of agent state passed to hooks."""
    messages: List[ChatMessage]         # current message history
    tools: List[Dict]                   # active tool definitions
    iteration: int                      # current loop iteration
    client: Optional[Any] = None        # ChatClient for hook-side LLM calls (e.g. compaction)


# ── Hook Protocol (all methods optional — missing = no-op) ────────────

@runtime_checkable
class AgentHooks(Protocol):
    """Optional hooks for the agent loop. Implement any subset."""

    # ── Tool call hooks ──────────────────────────────────────────────
    def before_tool_call(self, name: str, args: Dict[str, Any],
                         ctx: HookContext) -> Optional[BeforeToolCallResult]: ...
    def after_tool_call(self, name: str, args: Dict[str, Any],
                        result: str, is_error: bool,
                        ctx: HookContext) -> Optional[AfterToolCallResult]: ...

    # ── Turn-level hooks ─────────────────────────────────────────────
    def should_stop_after_turn(self, messages: List[ChatMessage],
                               ctx: HookContext) -> bool: ...

    # ── Context transform (before each LLM call) ─────────────────────
    def transform_context(self, messages: List[ChatMessage],
                          response_format: Optional[ResponseFormat],
                          ctx: HookContext) -> Optional[TransformContextResult]: ...



    # ── Observer (read-only) ─────────────────────────────────────────
    def on_event(self, event_type: str, data: Dict[str, Any]): ...


# ── Composite Hooks (chain multiple hook sets) ───────────────────────

class CompositeHooks:
    """Chain multiple AgentHooks. All hooks run in registration order."""

    def __init__(self, *hook_sets: Optional[AgentHooks]):
        self._hooks = [h for h in hook_sets if h is not None]

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

    # ── Turn-level hooks ─────────────────────────────────────────────

    def should_stop_after_turn(self, messages: List[ChatMessage],
                               ctx: HookContext) -> bool:
        for h in self._hooks:
            if getattr(h, 'should_stop_after_turn', lambda *a: False)(messages, ctx):
                return True
        return False

    # ── Context transform (chain: each hook sees previous output) ────

    def transform_context(self, messages: List[ChatMessage],
                          response_format: Optional[ResponseFormat],
                          ctx: HookContext) -> Optional[TransformContextResult]:
        cur_msgs = messages
        cur_rf = response_format
        for h in self._hooks:
            r = getattr(h, 'transform_context', lambda *a: None)(cur_msgs, cur_rf, ctx)
            if r:
                if r.messages is not None:
                    cur_msgs = r.messages
                if r.response_format is not None:
                    cur_rf = r.response_format
        if cur_msgs is not messages or cur_rf is not response_format:
            return TransformContextResult(messages=cur_msgs, response_format=cur_rf)
        return None

    # ── Observer ─────────────────────────────────────────────────────

    def on_event(self, event_type: str, data: Dict[str, Any]) -> None:
        for h in self._hooks:
            getattr(h, 'on_event', lambda *a: None)(event_type, data)
