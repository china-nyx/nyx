"""Default compaction hook — implemented using only generic hooks.

Uses ``transform_context`` to detect when context is too large, inject the
compaction instruction message, and override response_format.
Uses ``should_continue_after_text`` to intercept the summary text response,
parse it, replace history, and continue looping.

No loop-specific compaction code needed.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from sdk.agent_hooks import HookContext, TransformContextResult
from sdk.compaction import CompactionSettings, estimate_tokens, should_compact
from sdk.schemas import ChatMessage, JsonSchema, ResponseFormat

logger = logging.getLogger(__name__)


# ── Default compaction instruction ───────────────────────────────────

_DEFAULT_COMPACT_INSTRUCTION = """\
[CONTEXT WINDOW ALERT] Your context is approaching the limit.

Please organize your working memory:
1. Read your current memory files under `{memory_dir}/` (INDEX.md, identity.md, goals/, issues/, journal/)
2. Update them with what you've learned and accomplished so far
3. When done, return a concise summary of the session's progress

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


# ── Default Compaction Hook ──────────────────────────────────────────

class DefaultCompactionHook:
    """Default compaction hook — reproduces current NYX compaction behaviour.

    Uses only generic hooks (transform_context + should_continue_after_text).
    The loop has zero knowledge of compaction.

    Lifecycle:
    1. transform_context detects context is too large → injects instruction + schema
    2. Loop calls LLM → gets text response (summary JSON)
    3. should_continue_after_text intercepts the text response, parses summary,
       replaces history with initial_msgs + [summary], returns new msgs to continue
    """

    def __init__(self, settings: Any = None, context_window: int = 128_000):
        self._settings = settings or CompactionSettings()
        self._context_window = context_window

        # Internal state
        self._in_compaction_mode = False
        self._initial_messages: List[ChatMessage] = []
        self._last_compaction_msg_count = 0
        self._retry_short_summary = False

    def _memory_dir(self) -> str:
        return str(Path(os.getcwd()) / "memory")

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
        # Save initial messages on first call
        if not self._initial_messages:
            self._initial_messages = list(messages)

        # ── Phase 0: Check if we should enter compaction mode ─────────
        if not self._in_compaction_mode:
            _tokens = self._estimate_tokens(messages)
            if should_compact(_tokens, len(messages), self._context_window,
                              self._settings,
                              last_compaction_msg_count=self._last_compaction_msg_count):
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
            memory_dir = self._memory_dir()
            instruction = _DEFAULT_COMPACT_INSTRUCTION.format(memory_dir=memory_dir)
            new_msgs = list(messages) + [ChatMessage(role="user", content=instruction)]
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
        self._last_compaction_msg_count = len(self._initial_messages) + 1

        summary_msg = ChatMessage(role="user", content=(
            f"[COMPACTED HISTORY]\n{_summary}\n\n"
            f"Continue working from where you left off."
        ))
        new_msgs = list(self._initial_messages) + [summary_msg]
        logger.info(f"[compaction] done, summary={len(_summary)} chars, msgs now={len(new_msgs)}")
        return new_msgs
