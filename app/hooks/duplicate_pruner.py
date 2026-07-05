"""Replace duplicate tool output with a short token-saving placeholder."""
from __future__ import annotations

import hashlib
from typing import Any, Dict, Optional

from sdk.agent_hooks import AfterToolCallResult, HookContext


class DuplicateOutputPruner:
    """Replace duplicate tool output with a short token-saving placeholder."""

    def __init__(self):
        self._seen: set = set()

    def after_tool_call(self, name: str, args: Dict[str, Any],
                        result: str, is_error: bool,
                        ctx: HookContext) -> Optional[AfterToolCallResult]:
        raw = f"ERROR: {result}" if is_error else str(result)
        h = hashlib.md5(raw.encode(errors="replace")).hexdigest()
        if h in self._seen:
            return AfterToolCallResult(
                content=f"[DUPLICATE OUTPUT — same as a previous {name} call, "
                        f"skipping content to save tokens]"
            )
        self._seen.add(h)
        return None
