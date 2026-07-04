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

from sdk.schemas import ChatMessage, JsonSchema, ResponseFormat

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


# ── Compaction hook types ────────────────────────────────────────────

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

    # ── Compaction hooks ─────────────────────────────────────────────

    def should_compact_hook(self, context_tokens: int, msg_count: int,
                            context_window: int,
                            last_compaction_msg_count: int) -> Optional[bool]:
        """First hook returning a non-None value wins."""
        for h in self._hooks:
            r = getattr(h, 'should_compact_hook', lambda *a: None)(
                context_tokens, msg_count, context_window,
                last_compaction_msg_count)
            if r is not None:
                return r
        return None

    def build_compaction_instruction(self, memory_dir: str,
                                     messages: List[ChatMessage]) -> Optional[CompactionInstruction]:
        """First hook returning a non-None value wins."""
        for h in self._hooks:
            r = getattr(h, 'build_compaction_instruction', lambda *a: None)(
                memory_dir, messages)
            if r is not None:
                return r
        return None

    def apply_compaction_summary(self, raw_content: str,
                                 initial_messages: List[ChatMessage]) -> Optional[CompactionApplyResult]:
        """First hook returning a non-None value wins."""
        for h in self._hooks:
            r = getattr(h, 'apply_compaction_summary', lambda *a: None)(
                raw_content, initial_messages)
            if r is not None:
                return r
        return None

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


# ── Default compaction instruction ───────────────────────────────────

_DEFAULT_COMPACT_INSTRUCTION = """\
[CONTEXT WINDOW ALERT] Your context is approaching the limit.

Please organize your working memory:
1. Read your current memory files under `{memory_dir}/` (INDEX.md, identity.md, goals/, issues/, journal/)
2. Update them with what you've learned and accomplished so far
3. When done, return a concise summary of the session's progress

After this, your conversation history will be replaced with just your summary."""


def _default_compact_response_format() -> ResponseFormat:
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


# ── Default Compaction Hook (reproduces legacy NYX behaviour) ─────────

class DefaultCompactionHook:
    """Default compaction hook — reproduces current NYX compaction behaviour.

    Implements all three compaction hooks:
    - should_compact_hook: uses sdk.compaction.should_compact()
    - build_compaction_instruction: returns _DEFAULT_COMPACT_INSTRUCTION + schema
    - apply_compaction_summary: parses JSON {summary} and replaces history
    """

    def __init__(self, settings: Any = None):  # CompactionSettings
        from sdk.compaction import CompactionSettings as CS, should_compact as _sc
        self._settings = settings or CS()
        self._should_compact_fn = _sc
        self._last_compaction_msg_count = 0

    def should_compact_hook(self, context_tokens: int, msg_count: int,
                            context_window: int,
                            last_compaction_msg_count: int) -> Optional[bool]:
        return self._should_compact_fn(
            context_tokens, msg_count, context_window, self._settings,
            last_compaction_msg_count=last_compaction_msg_count)

    def build_compaction_instruction(self, memory_dir: str,
                                     messages: List[ChatMessage]) -> CompactionInstruction:
        return CompactionInstruction(
            instruction=_DEFAULT_COMPACT_INSTRUCTION.format(memory_dir=memory_dir),
            response_format=_default_compact_response_format().model_dump(exclude_none=True),
        )

    def apply_compaction_summary(self, raw_content: str,
                                 initial_messages: List[ChatMessage]) -> Optional[CompactionApplyResult]:
        # Parse JSON {summary} or use raw content
        _summary = raw_content
        try:
            _parsed = json.loads(raw_content)
            _summary = _parsed.get("summary", raw_content)
        except (json.JSONDecodeError, TypeError):
            pass

        if not _summary or len(_summary.strip()) < 20:
            # Signal that summary is too short — loop will re-prompt
            return None

        summary_msg = ChatMessage(role="user", content=(
            f"[COMPACTED HISTORY]\n{_summary}\n\n"
            f"Continue working from where you left off."
        ))
        self._last_compaction_msg_count = len(initial_messages) + 1
        return CompactionApplyResult(
            messages=list(initial_messages) + [summary_msg])


# ── Helpers ───────────────────────────────────────────────────────────

def _make_args_key(tool_name: str, args: dict) -> tuple:
    """Create a hashable key from (tool_name, args) for call deduplication."""
    if not isinstance(args, dict):
        args = {}
    sorted_args = json.dumps(args, sort_keys=True, default=str)
    return (tool_name, hashlib.md5(sorted_args.encode()).hexdigest())
