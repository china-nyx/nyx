"""Stop agent after a terminal tool succeeds."""
from __future__ import annotations

from typing import Any, Dict, Optional

from sdk.agent_hooks import AfterToolCallResult, HookContext


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
