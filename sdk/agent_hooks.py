"""Agent hooks — pluggable interceptors for the agent loop.

Inspired by pi's AgentHarness design (packages/agent/docs/hooks.md).
Each hook is a Python object with optional methods. Chain multiple hooks
with CompositeHooks.

Built-in hooks reproduce current NYX behaviour out of the box so callers
need zero changes when they pass ``hooks=None`` to ``run_agent()``.
"""
from __future__ import annotations

import hashlib
import json
import logging
from collections import deque
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Protocol, runtime_checkable

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

    def before_tool_call(self, name: str, args: Dict[str, Any],
                         ctx: HookContext) -> Optional[BeforeToolCallResult]: ...
    def after_tool_call(self, name: str, args: Dict[str, Any],
                        result: str, is_error: bool,
                        ctx: HookContext) -> Optional[AfterToolCallResult]: ...
    def should_stop_after_turn(self, messages: List[ChatMessage],
                               ctx: HookContext) -> bool: ...
    def on_event(self, event_type: str, data: Dict[str, Any]): ...


# ── Composite Hooks (chain multiple hook sets) ───────────────────────

class CompositeHooks:
    """Chain multiple AgentHooks. All hooks run in registration order."""

    def __init__(self, *hook_sets: Optional[AgentHooks]):
        self._hooks = [h for h in hook_sets if h is not None]

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

    def should_stop_after_turn(self, messages: List[ChatMessage],
                               ctx: HookContext) -> bool:
        for h in self._hooks:
            if getattr(h, 'should_stop_after_turn', lambda *a: False)(messages, ctx):
                return True
        return False

    def on_event(self, event_type: str, data: Dict[str, Any]) -> None:
        for h in self._hooks:
            getattr(h, 'on_event', lambda *a: None)(event_type, data)


# ── Built-in hook implementations ────────────────────────────────────

class RepetitiveCallGuard:
    """Block N consecutive identical tool calls (current NYX behaviour)."""

    def __init__(self, threshold: int = 3, window: int = 10):
        self._threshold = threshold
        self._history: deque = deque(maxlen=window)
        self._consecutive = 0
        self._last_key = None
        self._cached: Dict[str, str] = {}

    def before_tool_call(self, name: str, args: Dict[str, Any],
                         ctx: HookContext) -> Optional[BeforeToolCallResult]:
        key = _make_args_key(name, args)
        if key == self._last_key:
            self._consecutive += 1
        else:
            self._consecutive = 1
            self._last_key = key

        if self._consecutive >= self._threshold:
            cached = self._cached.get(key, "(no cached result)")
            reason = (
                f"[REPETITIVE CALL GUARD] You have run this identical command "
                f"{self._consecutive} times in a row. Do NOT repeat it. "
                f"Use the result you already have, try a DIFFERENT approach."
            )
            return BeforeToolCallResult(block=True, reason=f"{cached}\n\n{reason}")
        return None

    def after_tool_call(self, name: str, args: Dict[str, Any],
                        result: str, is_error: bool,
                        ctx: HookContext) -> Optional[AfterToolCallResult]:
        if not is_error:
            key = _make_args_key(name, args)
            self._cached[key] = str(result)
            self._history.append(key)
        return None


class DuplicateOutputPruner:
    """Replace duplicate tool output with a short token-saving placeholder."""

    def __init__(self):
        self._seen: set = set()

    def after_tool_call(self, name: str, args: Dict[str, Any],
                        result: str, is_error: bool,
                        ctx: HookContext) -> Optional[AfterToolCallResult]:
        raw = f"ERROR: {result}" if is_error else str(result)
        h = hashlib.md5(raw.encode(errors="replace")).hexdigest()
        if h in self._seen:
            return AfterToolCallResult(
                content=f"[DUPLICATE OUTPUT — same as a previous {name} call, "
                        f"skipping content to save tokens]"
            )
        self._seen.add(h)
        return None


class TerminalToolHook:
    """Stop agent after a terminal tool succeeds (current NYX behaviour)."""

    def __init__(self, terminal_tools: set):
        self._terminal = terminal_tools

    def after_tool_call(self, name: str, args: Dict[str, Any],
                        result: str, is_error: bool,
                        ctx: HookContext) -> Optional[AfterToolCallResult]:
        if name in self._terminal and not is_error:
            return AfterToolCallResult(terminate=True)
        return None


class StepLogger:
    """Log each tool call (replaces on_step callback)."""

    def __init__(self, on_step: Callable[[str, Dict, str, bool], None]):
        self._on_step = on_step

    def after_tool_call(self, name: str, args: Dict[str, Any],
                        result: str, is_error: bool,
                        ctx: HookContext) -> Optional[AfterToolCallResult]:
        self._on_step(name, args, result, is_error)
        return None


# ── Helpers ───────────────────────────────────────────────────────────

def _make_args_key(tool_name: str, args: dict) -> tuple:
    """Create a hashable key from (tool_name, args) for call deduplication."""
    if not isinstance(args, dict):
        args = {}
    sorted_args = json.dumps(args, sort_keys=True, default=str)
    return (tool_name, hashlib.md5(sorted_args.encode()).hexdigest())
