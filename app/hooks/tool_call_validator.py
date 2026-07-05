"""Tool call validator — drops tool calls with malformed JSON arguments.

Plugs into ``before_tool_calls`` so that invalid tool calls never enter the
message history (preventing HTTP 500 from llama-server).  If all tool calls
in a turn are invalid the hook signals the agent loop to skip the turn
entirely and re-prompt the model.

This is an app-layer hook because it targets a local-model quirk; other
deployments may not need it.
"""
from __future__ import annotations

import logging
from typing import List, Optional

from sdk.agent_hooks import (
    HookContext,
    BeforeToolCallsResult,
)
from sdk.schemas import ChatResponseMessage

logger = logging.getLogger(__name__)


class ToolCallValidator:
    """Filter out tool calls whose ``arguments`` field is not valid JSON."""

    def before_tool_calls(self, message: ChatResponseMessage,
                          tool_calls: List,
                          ctx: HookContext) -> Optional[BeforeToolCallsResult]:
        _valid: list = []
        _dropped: list[str] = []

        for tc in tool_calls:
            if tc.parse_arguments() is not None:
                _valid.append(tc)
            else:
                _dropped.append(tc.function.name)
                logger.warning(
                    f"[tool-call-validator] dropping malformed call "
                    f"\"{tc.function.name}\": {tc.function.arguments[:120]}…")

        if not _dropped:
            return None  # nothing to do

        if not _valid:
            logger.warning(
                f"[tool-call-validator] all calls invalid ({_dropped}), "
                f"skipping turn")
            return BeforeToolCallsResult(tool_calls=None)

        logger.info(
            f"[tool-call-validator] kept {len(_valid)}, "
            f"dropped {len(_dropped)}")
        return BeforeToolCallsResult(tool_calls=_valid)
