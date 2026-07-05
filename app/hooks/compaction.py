"""Compaction hook — compresses conversation history when context window is full.

Plugs into the agent loop via ``before_llm_call`` — when tokens exceed the
threshold it fires a **separate LLM call** to generate a summary, then returns
compacted messages.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from sdk.agent_hooks import HookContext
from sdk.schemas import ChatMessage

logger = logging.getLogger(__name__)


# ── Settings (injected by caller) ───────────────────────────────────

@dataclass(frozen=True)
class CompactionSettings:
    """Knobs controlling compaction behaviour."""

    enabled: bool = True
    reserve_tokens: int = 16384       # trigger when remaining headroom < this many tokens


# ── Token estimation (private helpers) ──────────────────────────

def _estimate_tokens(text: str) -> int:
    """Estimate token count from text using chars/4 heuristic."""
    return max(1, len(text) // 4) if text else 0


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
    total = _estimate_tokens(m.content or "")
    for tc in (m.tool_calls or []):
        fn = tc.get("function", {}) if isinstance(tc, dict) else {}
        total += _estimate_tokens(fn.get("arguments", "") or "")
    return total


# ── Compaction Hook ───────────────────────────────────────────────────

class CompactionHook:
    """Compaction hook — compresses conversation history when context window is full.

    Uses ``before_llm_call``.  When tokens exceed the threshold it fires a
    **separate LLM call** (via ctx.llm) to generate the summary, then returns
    compacted messages directly.

    All messages are serialized into text and sent in a single user prompt —
    no JSON schema, no tail-keeping.  The model sees everything and produces
    a plain-text summary that replaces the full history.
    """

    def __init__(self, settings: Any = None, context_window: int = 256_000):
        self._settings = settings or CompactionSettings()
        self._context_window = context_window

        # Internal state
        self._system_message: Optional[ChatMessage] = None

    def _estimate_tokens(self, messages: List[ChatMessage]) -> int:
        return sum(_estimate_msg_tokens(m) for m in messages)

    # ── before_llm_call: detect + execute compaction inline ──────────

    def before_llm_call(self, messages: List[ChatMessage],
                        ctx: HookContext) -> Optional[List[ChatMessage]]:
        # Capture system message on first call (messages[0] is the system prompt)
        if self._system_message is None:
            self._system_message = messages[0]

        _tokens = self._estimate_tokens(messages)
        if not should_compact(_tokens, self._context_window, self._settings):
            return None

        logger.info(
            f"[compaction] triggered (tokens={_tokens}, msgs={len(messages)})")

        # No client available — cannot compact, fall through
        _llm = ctx.llm
        if _llm is None:
            logger.warning("[compaction] no llm in HookContext, skipping")
            return None

        # Serialize all messages (skip system prompt at index 0)
        conversation_text = _serialize_messages(messages[1:])

        # Build a small compaction request (system + one user message)
        compact_prompt = _COMPACT_USER_TEMPLATE.format(
            conversation_text=conversation_text,
        )
        compact_msgs: List[ChatMessage] = [
            ChatMessage(role="system", content=_COMPACT_SYSTEM_PROMPT),
            ChatMessage(role="user", content=compact_prompt),
        ]

        # Fire separate LLM call for summary (not through agent loop)
        from app.config import config as _config
        try:
            resp = _llm.chat(
                compact_msgs,
                model=_config.llm_model,
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

        # Replace entire history with system + summary
        summary_msg = ChatMessage(role="user", content=(
            f"[COMPACTED HISTORY]\n{_summary}\n\n"
            f"Continue working from where you left off."
        ))
        new_msgs = [self._system_message, summary_msg]
        logger.info(f"[compaction] done, summary={len(_summary)} chars, msgs now={len(new_msgs)}")
        return new_msgs
