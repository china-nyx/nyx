"""Block N consecutive identical tool calls."""
from __future__ import annotations

import hashlib
import json
from collections import deque
from typing import Any, Dict, Optional

from sdk.agent_hooks import (
    AfterToolCallResult,
    BeforeToolCallResult,
    HookContext,
)


class RepetitiveCallGuard:
    """Block N consecutive identical tool calls (current NYX behaviour)."""

    def __init__(self, threshold: int = 3, window: int = 10):
        self._threshold = threshold
        self._history: deque = deque(maxlen=window)
        self._consecutive = 0
        self._last_key = None
        self._cached: Dict[str, str] = {}

    def before_tool_call(self, name: str, args: Dict[str, Any],
                         ctx: HookContext) -> Optional[BeforeToolCallResult]:
        key = _make_args_key(name, args)
        if key == self._last_key:
            self._consecutive += 1
        else:
            self._consecutive = 1
            self._last_key = key

        if self._consecutive >= self._threshold:
            cached = self._cached.get(key, "(no cached result)")
            reason = (
                f"[REPETITIVE CALL GUARD] You have run this identical command "
                f"{self._consecutive} times in a row. Do NOT repeat it. "
                f"Use the result you already have, try a DIFFERENT approach."
            )
            return BeforeToolCallResult(block=True, reason=f"{cached}\n\n{reason}")
        return None

    def after_tool_call(self, name: str, args: Dict[str, Any],
                        result: str, is_error: bool,
                        ctx: HookContext) -> Optional[AfterToolCallResult]:
        if not is_error:
            key = _make_args_key(name, args)
            self._cached[key] = str(result)
            self._history.append(key)
        return None


def _make_args_key(tool_name: str, args: dict) -> tuple:
    """Create a hashable key from (tool_name, args)."""
    if not isinstance(args, dict):
        args = {}
    sorted_args = json.dumps(args, sort_keys=True, default=str)
    return (tool_name, hashlib.md5(sorted_args.encode()).hexdigest())
