"""Context compaction — token estimation and trigger detection.

Pure functions. No dependency on app/config or sdk/tools.
All behaviour knobs are passed explicitly via ``CompactionSettings``.
"""
import logging
from dataclasses import dataclass
from typing import Dict, List

logger = logging.getLogger(__name__)


# ── Settings (injected by caller) ───────────────────────────────────

@dataclass(frozen=True)
class CompactionSettings:
    """Knobs controlling compaction behaviour."""

    enabled: bool = True
    reserve_tokens: int = 16384       # trigger when remaining < this many tokens
    compact_at: int = 100             # msg_count threshold to trigger compaction
    cooldown_messages: int = 10       # min new msgs since last compaction before re-trigger


# ── Token estimation ────────────────────────────────────────────────


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


def should_compact(context_tokens: int, msg_count: int,
                   context_window: int, settings: CompactionSettings,
                   last_compaction_msg_count: int = 0) -> bool:
    """Check if compaction should trigger based on token count or message count.

    Args:
        last_compaction_msg_count: msg_count at the time of the last compaction.
            If set, compaction won't re-trigger until at least ``cooldown_messages``
            new messages have accumulated since then (prevents tight-loop re-firing).
    """
    if not settings.enabled:
        return False

    # Token-based trigger (always urgent — no cooldown)
    token_triggered = context_tokens > (context_window - settings.reserve_tokens)

    # Message-count trigger (subject to cooldown)
    msg_triggered = msg_count > settings.compact_at
    if msg_triggered and last_compaction_msg_count > 0:
        if (msg_count - last_compaction_msg_count) < settings.cooldown_messages:
            msg_triggered = False

    return token_triggered or msg_triggered
