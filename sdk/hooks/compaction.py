"""Compaction — token estimation, trigger detection, and hook implementation.

Pure functions (estimate_tokens, clamp_max_tokens, should_compact) are used by
agent.py for context management.  CompactionHook plugs into the agent loop
via transform_context + should_continue_after_text.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from sdk.agent_hooks import HookContext, TransformContextResult
from sdk.schemas import ChatMessage, JsonSchema, ResponseFormat

logger = logging.getLogger(__name__)


# ── Settings (injected by caller) ───────────────────────────────────

@dataclass(frozen=True)
class CompactionSettings:
    """Knobs controlling compaction behaviour."""

    enabled: bool = True
    reserve_tokens: int = 16384       # trigger when remaining headroom < this many tokens


# ── Token estimation (pure functions) ───────────────────────────────

def estimate_tokens(text: str) -> int:
    """Estimate token count from text using chars/4 heuristic."""
    return max(1, len(text) // 4) if text else 0


def estimate_context_tokens(messages: List[Dict]) -> int:
    """Sum estimated tokens across all messages in the conversation."""
    total = 0
    for msg in messages:
        content = msg.get("content", "") or ""
        total += estimate_tokens(content)
        for tc in msg.get("tool_calls") or []:
            fn = tc.get("function", {})
            total += estimate_tokens(fn.get("arguments", "") or "")
    return total


def clamp_max_tokens(requested: int, context_tokens: int, context_window: int) -> int:
    """Clamp max_tokens so the total (context + output) stays within the window.

    Leaves a 4096-token safety margin beyond the reserve to avoid OOM / truncation.
    Returns at least 256 so we never request zero tokens.
    """
    headroom = context_window - context_tokens - 4096
    return max(256, min(requested, headroom))


def should_compact(context_tokens: int, context_window: int,
                   settings: CompactionSettings) -> bool:
    """Check if compaction should trigger based on remaining headroom."""
    if not settings.enabled:
        return False
    return context_tokens > (context_window - settings.reserve_tokens)


# ── Default compaction instruction ───────────────────────────────────

_DEFAULT_COMPACT_INSTRUCTION = """\
[CONTEXT WINDOW ALERT] Your context is approaching the limit.

Please provide a concise summary of the session's progress so far:
- What task(s) you are working on
- Key decisions made and actions taken
- Current status and next steps

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


# ── Compaction Hook ───────────────────────────────────────────────────

class CompactionHook:
    """Compaction hook — compresses conversation history when context window is full.

    Uses only generic hooks (transform_context + should_continue_after_text).
    The loop has zero knowledge of compaction.

    Lifecycle:
    1. transform_context detects tokens approaching limit → injects summary instruction
    2. Loop calls LLM → gets text response (summary JSON)
    3. should_continue_after_text intercepts the text response, parses summary,
       replaces history with [system message] + [summary], returns new msgs to continue
    """

    def __init__(self, settings: Any = None, context_window: int = 256_000):
        self._settings = settings or CompactionSettings()
        self._context_window = context_window

        # Internal state
        self._in_compaction_mode = False
        self._system_message: Optional[ChatMessage] = None
        self._retry_short_summary = False

    def _estimate_tokens(self, messages: List[ChatMessage]) -> int:
        total = 0
        for m in messages:
            c = m.content or ""
            total += estimate_tokens(c)
            for tc in (m.tool_calls or []):
                fn = tc.get("function", {}) if isinstance(tc, dict) else {}
                total += estimate_tokens(fn.get("arguments", "") or "")
        return total

    # ── transform_context: trigger + inject instruction / keep schema ──

    def transform_context(self, messages: List[ChatMessage],
                          response_format: Optional[ResponseFormat],
                          ctx: HookContext) -> Optional[TransformContextResult]:
        # Capture system message on first call (messages[0] is the system prompt)
        if self._system_message is None:
            self._system_message = messages[0]

        # ── Phase 0: Check if we should enter compaction mode ─────────
        if not self._in_compaction_mode:
            _tokens = self._estimate_tokens(messages)
            if should_compact(_tokens, self._context_window, self._settings):
                self._in_compaction_mode = True
                logger.info(
                    f"[compaction] triggered (tokens={_tokens}, msgs={len(messages)})")

        if not self._in_compaction_mode:
            return None

        # ── Phase 1: Retry short summary ───────────────────────────────
        if self._retry_short_summary:
            new_msgs = list(messages) + [ChatMessage(
                role="user",
                content="Your summary is too short. Please provide a meaningful "
                        "summary of the session's progress and call result again.")
            ]
            rf = _compact_response_format()
            return TransformContextResult(messages=new_msgs, response_format=rf)

        # ── Phase 2: Inject compaction instruction (first time) ────────
        has_instruction = (
            messages and messages[-1].role == "user"
            and "[CONTEXT WINDOW ALERT]" in (messages[-1].content or "")
        )

        if not has_instruction:
            new_msgs = list(messages) + [ChatMessage(role="user", content=_DEFAULT_COMPACT_INSTRUCTION)]
            rf = _compact_response_format()
            return TransformContextResult(messages=new_msgs, response_format=rf)

        # ── Phase 3: Keep compaction schema active for next LLM call ───
        rf = _compact_response_format()
        return TransformContextResult(response_format=rf)

    # ── should_continue_after_text: parse summary and replace history ──

    def should_continue_after_text(self, content: str, messages: List[ChatMessage],
                                   ctx: HookContext) -> Optional[List[ChatMessage]]:
        """Called by loop when LLM returns text (no tool calls).

        Return a new message list to continue looping with, or None to exit normally.
        """
        if not self._in_compaction_mode:
            return None

        # Parse summary from JSON response
        _summary = content
        try:
            _parsed = json.loads(content)
            _summary = _parsed.get("summary", content)
        except (json.JSONDecodeError, TypeError):
            pass

        if not _summary or len(_summary.strip()) < 20:
            self._retry_short_summary = True
            logger.info("[compaction] summary too short, retrying")
            return list(messages)

        # Success — replace history with initial + summary
        self._in_compaction_mode = False
        self._retry_short_summary = False

        summary_msg = ChatMessage(role="user", content=(
            f"[COMPACTED HISTORY]\n{_summary}\n\n"
            f"Continue working from where you left off."
        ))
        new_msgs = [self._system_message, summary_msg]
        logger.info(f"[compaction] done, summary={len(_summary)} chars, msgs now={len(new_msgs)}")
        return new_msgs
