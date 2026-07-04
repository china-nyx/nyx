"""Agent hooks — protocol, result types, and composition.

All hook *implementations* live in ``sdk/hooks/<name>.py`` (one per file).
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
class AfterToolCallResult:
    """Return from after_tool_call to modify the tool result.

    Omitted fields (None) keep their original values.
    """
    content: Optional[str] = None       # replace result content
    is_error: Optional[bool] = None     # replace error flag
    terminate: bool = False             # stop agent after this batch


@dataclass(frozen=True)
class CompactionInstruction:
    """Return from build_compaction_instruction to customise compaction mode.

    The loop uses *instruction* as the user message injected into history,
    and *response_format* as the JSON schema passed to the LLM.
    Return ``None`` to keep the built-in defaults.
    """
    instruction: str
    response_format: Optional[Dict] = None


@dataclass(frozen=True)
class CompactionApplyResult:
    """Return from apply_compaction_summary to replace history after compaction.

    *messages* is the new message list (typically ``initial_msgs + [summary_msg]``).
    Return ``None`` to keep the built-in behaviour.
    """
    messages: List[ChatMessage]


# ── Hook context (snapshot passed to each hook) ───────────────────────

@dataclass
class HookContext:
    """Snapshot of agent state passed to hooks."""
    messages: List[ChatMessage]         # current message history
    tools: List[Dict]                   # active tool definitions
    iteration: int                      # current loop iteration


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

    # ── Compaction hooks ─────────────────────────────────────────────
    def should_compact_hook(self, context_tokens: int, msg_count: int,
                            context_window: int,
                            last_compaction_msg_count: int) -> Optional[bool]: ...
    def build_compaction_instruction(self, memory_dir: str,
                                     messages: List[ChatMessage]) -> Optional[CompactionInstruction]: ...
    def apply_compaction_summary(self, raw_content: str,
                                 initial_messages: List[ChatMessage]) -> Optional[CompactionApplyResult]: ...

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

    # ── Compaction hooks (first non-None wins) ───────────────────────

    def should_compact_hook(self, context_tokens: int, msg_count: int,
                            context_window: int,
                            last_compaction_msg_count: int) -> Optional[bool]:
        for h in self._hooks:
            r = getattr(h, 'should_compact_hook', lambda *a: None)(
                context_tokens, msg_count, context_window,
                last_compaction_msg_count)
            if r is not None:
                return r
        return None

    def build_compaction_instruction(self, memory_dir: str,
                                     messages: List[ChatMessage]) -> Optional[CompactionInstruction]:
        for h in self._hooks:
            r = getattr(h, 'build_compaction_instruction', lambda *a: None)(
                memory_dir, messages)
            if r is not None:
                return r
        return None

    def apply_compaction_summary(self, raw_content: str,
                                 initial_messages: List[ChatMessage]) -> Optional[CompactionApplyResult]:
        for h in self._hooks:
            r = getattr(h, 'apply_compaction_summary', lambda *a: None)(
                raw_content, initial_messages)
            if r is not None:
                return r
        return None

    # ── Observer ─────────────────────────────────────────────────────

    def on_event(self, event_type: str, data: Dict[str, Any]) -> None:
        for h in self._hooks:
            getattr(h, 'on_event', lambda *a: None)(event_type, data)
