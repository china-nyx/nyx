"""Post-task reflection hook — injects a reflection turn after task completion."""
from __future__ import annotations

from typing import Any, Dict, Optional

from sdk.agent_hooks import AfterLlmCallResult, HookContext
from sdk.schemas import ChatMessage


class PostTaskReflectHook:
    """Inject one reflection turn after the agent finishes (no tool calls).

    Implements ``after_llm_call`` — when the model returns a text-only
    response, this hook tells the agent loop to continue with a user
    message containing the reflection prompt. The final reflection text
    is available via ``reflection`` after the session ends.
    """

    def __init__(self, prompt: str):
        self._prompt = prompt
        self._used = False
        self.reflection: Optional[str] = None

    def after_llm_call(self, message: Dict[str, Any],
                       ctx: HookContext) -> Optional[AfterLlmCallResult]:
        if self._used:
            # Second text-only turn — this is the reflection response
            self.reflection = message.get("content") or ""
            return None
        # Trigger only on first text-only turn (no tool calls)
        if not message.get("tool_calls"):
            self._used = True
            return AfterLlmCallResult(
                continue_loop=True,
                messages_to_append=[
                    ChatMessage(role="user", content=self._prompt),
                ],
            )
        return None
