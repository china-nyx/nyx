"""Compaction — token estimation, trigger detection, and hook implementation.

Pure functions (estimate_tokens, clamp_max_tokens, should_compact) are used by
agent.py for context management.  CompactionHook plugs into the agent loop
via ``transform_context`` only — when tokens exceed the threshold it fires a
**separate LLM call** (not through the agent loop) to generate the summary,
then returns compacted messages.  This mirrors pi's _checkCompaction pattern.
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

_COMPACT_SYSTEM_PROMPT = "You are a helpful assistant that summarizes conversations."

_COMPACT_USER_PROMPT = """\
Summarize the conversation above into a concise summary. Include:
- What task(s) you are working on
- Key decisions made and actions taken
- Current status and next steps

Return ONLY valid JSON with this shape: {"summary": "<your summary here>"}"""


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

    Uses only ``transform_context``.  When tokens exceed the threshold it fires a
    **separate LLM call** (via ctx.client) to generate the summary, then returns
    compacted messages directly.  No agent-loop round-trip needed — mirrors pi's
    _checkCompaction pattern.

    Lifecycle:
    1. transform_context detects tokens approaching limit
    2. Fires a direct LLM call (ctx.client.chat) with compaction prompt + schema
    3. Parses summary, returns [system_msg, compacted_user_msg] to replace history
    """

    def __init__(self, settings: Any = None, context_window: int = 256_000):
        self._settings = settings or CompactionSettings()
        self._context_window = context_window

        # Internal state
        self._system_message: Optional[ChatMessage] = None

    def _estimate_tokens(self, messages: List[ChatMessage]) -> int:
        total = 0
        for m in messages:
            c = m.content or ""
            total += estimate_tokens(c)
            for tc in (m.tool_calls or []):
                fn = tc.get("function", {}) if isinstance(tc, dict) else {}
                total += estimate_tokens(fn.get("arguments", "") or "")
        return total

    # ── transform_context: detect + execute compaction inline ────────

    def transform_context(self, messages: List[ChatMessage],
                          response_format: Optional[ResponseFormat],
                          ctx: HookContext) -> Optional[TransformContextResult]:
        # Capture system message on first call (messages[0] is the system prompt)
        if self._system_message is None:
            self._system_message = messages[0]

        _tokens = self._estimate_tokens(messages)
        if not should_compact(_tokens, self._context_window, self._settings):
            return None

        logger.info(
            f"[compaction] triggered (tokens={_tokens}, msgs={len(messages)})")

        # No client available — cannot compact, fall through
        client = ctx.client
        if client is None:
            logger.warning("[compaction] no client in HookContext, skipping")
            return None

        # Build compaction messages: system + all history + instruction
        compact_msgs: List[ChatMessage] = [
            ChatMessage(role="system", content=_COMPACT_SYSTEM_PROMPT),
            *messages,
            ChatMessage(role="user", content=_COMPACT_USER_PROMPT),
        ]

        # Fire separate LLM call for summary (not through agent loop)
        try:
            resp = client.chat(
                compact_msgs,
                temperature=0.3,
                max_tokens=2048,
                response_format=_compact_response_format(),
            )
        except Exception as exc:
            logger.error(f"[compaction] LLM call failed: {exc}")
            return None

        if not resp.choices:
            logger.warning("[compaction] empty compaction response")
            return None

        raw_content = resp.choices[0].message.content or ""

        # Parse summary from JSON response
        _summary = raw_content.strip()
        try:
            _parsed = json.loads(_summary)
            _summary = _parsed.get("summary", _summary)
        except (json.JSONDecodeError, TypeError):
            pass

        if not _summary or len(_summary.strip()) < 20:
            logger.info("[compaction] summary too short, skipping")
            return None

        # Success — replace history with system + compacted summary
        summary_msg = ChatMessage(role="user", content=(
            f"[COMPACTED HISTORY]\n{_summary}\n\n"
            f"Continue working from where you left off."
        ))
        new_msgs = [self._system_message, summary_msg]
        logger.info(f"[compaction] done, summary={len(_summary)} chars, msgs now={len(new_msgs)}")
        return TransformContextResult(messages=new_msgs)
