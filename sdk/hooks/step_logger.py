"""Log each tool call (replaces on_step callback)."""
from __future__ import annotations

from typing import Any, Callable, Dict, Optional

from sdk.agent_hooks import AfterToolCallResult, HookContext


class StepLogger:
    """Log each tool call (replaces on_step callback)."""

    def __init__(self, on_step: Callable[[str, Dict, str, bool], None]):
        self._on_step = on_step

    def after_tool_call(self, name: str, args: Dict[str, Any],
                        result: str, is_error: bool,
                        ctx: HookContext) -> Optional[AfterToolCallResult]:
        self._on_step(name, args, result, is_error)
        return None
