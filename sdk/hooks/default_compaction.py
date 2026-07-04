"""Default compaction hook — reproduces legacy NYX behaviour."""
from __future__ import annotations

import json
import logging
from typing import Any, List, Optional

from sdk.agent_hooks import (
    CompactionApplyResult,
    CompactionInstruction,
    HookContext,
)
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


# ── Default Compaction Hook ──────────────────────────────────────────

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
