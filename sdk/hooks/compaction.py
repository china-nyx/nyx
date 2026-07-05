"""Compaction — token estimation, trigger detection, and hook implementation.

Pure functions (estimate_tokens, clamp_max_tokens, should_compact) are used by
agent.py for context management.  CompactionHook plugs into the agent loop
via ``transform_context`` only — when tokens exceed the threshold it fires a
**separate LLM call** (not through the agent loop) to generate the summary,
then returns compacted messages.

Strategy: keep the most recent K messages intact (recent work context),
serialize older messages into text, send that text in a single user prompt
to the compaction LLM call.  The result replaces only the old portion.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from sdk.agent_hooks import HookContext, TransformContextResult
from sdk.schemas import ChatMessage

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


# ── Compaction prompt ────────────────────────────────────────────────

_COMPACT_SYSTEM_PROMPT = "You are a helpful assistant that summarizes conversations."

_COMPACT_USER_TEMPLATE = """\
Below is a conversation transcript. Summarize it concisely in English, covering:
- What task(s) were being worked on
- Key decisions made and actions taken
- Current status and next steps

<conversation>
{conversation_text}
</conversation>"""


# ── Helpers ───────────────────────────────────────────────────────────

def _serialize_messages(messages: List[ChatMessage]) -> str:
    """Serialize a list of ChatMessages into readable text for summarization."""
    parts = []
    for m in messages:
        role = m.role.upper()
        content = m.content or ""
        if m.tool_calls:
            for tc in m.tool_calls:
                fn = tc.get("function", {}) if isinstance(tc, dict) else {}
                name = fn.get("name", "unknown")
                args = fn.get("arguments", "{}")
                parts.append(f"[{role}] calling {name}({args})")
        elif m.tool_call_id:
            parts.append(f"[TOOL RESULT] {content}")
        else:
            parts.append(f"[{role}] {content}")
    return "\n\n".join(parts)


def _estimate_msg_tokens(m: ChatMessage) -> int:
    """Estimate tokens for a single message."""
    total = estimate_tokens(m.content or "")
    for tc in (m.tool_calls or []):
        fn = tc.get("function", {}) if isinstance(tc, dict) else {}
        total += estimate_tokens(fn.get("arguments", "") or "")
    return total


# ── Compaction Hook ───────────────────────────────────────────────────

class CompactionHook:
    """Compaction hook — compresses conversation history when context window is full.

    Uses only ``transform_context``.  When tokens exceed the threshold it fires a
    **separate LLM call** (via ctx.client) to generate the summary, then returns
    compacted messages directly.

    Strategy: keep recent K messages intact, serialize older ones into text,
    send that text in a single user prompt for summarization.  The summary
    replaces only the old portion — recent context is preserved.
    """

    # Number of recent messages to keep untouched (recent work context)
    _KEEP_TAIL_TOKENS = 8192  # keep ~8K tokens of recent messages

    def __init__(self, settings: Any = None, context_window: int = 256_000):
        self._settings = settings or CompactionSettings()
        self._context_window = context_window

        # Internal state
        self._system_message: Optional[ChatMessage] = None

    def _estimate_tokens(self, messages: List[ChatMessage]) -> int:
        return sum(_estimate_msg_tokens(m) for m in messages)

    # ── transform_context: detect + execute compaction inline ────────

    def transform_context(self, messages: List[ChatMessage],
                          response_format: Optional[Any],
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

        # Split messages into [old to summarize] + [recent to keep]
        # Keep the tail that fits within _KEEP_TAIL_TOKENS
        old_msgs, recent_msgs = self._split_messages(messages)
        if not old_msgs:
            logger.info("[compaction] nothing to compress")
            return None

        conversation_text = _serialize_messages(old_msgs)

        # Build a small compaction request (system + one user message)
        compact_prompt = _COMPACT_USER_TEMPLATE.format(
            conversation_text=conversation_text,
        )
        compact_msgs: List[ChatMessage] = [
            ChatMessage(role="system", content=_COMPACT_SYSTEM_PROMPT),
            ChatMessage(role="user", content=compact_prompt),
        ]

        # Fire separate LLM call for summary (not through agent loop)
        try:
            resp = client.chat(
                compact_msgs,
                temperature=0.3,
                max_tokens=min(4096, self._settings.reserve_tokens),
            )
        except Exception as exc:
            logger.error(f"[compaction] LLM call failed: {exc}")
            return None

        if not resp.choices:
            logger.warning("[compaction] empty compaction response")
            return None

        _summary = (resp.choices[0].message.content or "").strip()
        # Try JSON parse in case model wraps it
        try:
            _parsed = json.loads(_summary)
            if isinstance(_parsed, dict):
                _summary = _parsed.get("summary", _summary)
        except (json.JSONDecodeError, TypeError):
            pass

        if not _summary or len(_summary.strip()) < 20:
            logger.info("[compaction] summary too short, skipping")
            return None

        # Build new message list: system + summary + recent tail
        summary_msg = ChatMessage(role="user", content=(
            f"[COMPACTED HISTORY]\n{_summary}\n\n"
            f"Continue working from where you left off."
        ))
        new_msgs = [self._system_message, summary_msg] + recent_msgs
        logger.info(
            f"[compaction] done, summary={len(_summary)} chars, "
            f"msgs now={len(new_msgs)} (kept {len(recent_msgs)} recent)")
        return TransformContextResult(messages=new_msgs)

    def _split_messages(self, messages: List[ChatMessage]):
        """Split into (old_to_summarize, recent_to_keep).

        Skip the system message (index 0).  Keep a tail of messages whose
        combined token count fits within _KEEP_TAIL_TOKENS.
        """
        if len(messages) <= 2:
            return [], list(messages)

        # messages[0] is system, start from index 1
        content_msgs = messages[1:]

        # Walk from the end, accumulating tokens until we hit the keep limit
        tail_start = len(content_msgs)
        tail_tokens = 0
        for i in range(len(content_msgs) - 1, -1, -1):
            mt = _estimate_msg_tokens(content_msgs[i])
            if tail_tokens + mt > self._KEEP_TAIL_TOKENS:
                break
            tail_start = i
            tail_tokens += mt

        old = content_msgs[:tail_start]
        recent = content_msgs[tail_start:]
        return old, recent
