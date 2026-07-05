"""Post-task reflection hook — injects a reflection turn after task completion."""
from __future__ import annotations

import logging
from typing import Optional

from sdk.agent_hooks import BeforeTurnEndResult, HookContext
from sdk.schemas import ChatMessage, ChatResponseMessage

logger = logging.getLogger(__name__)


class TaskReflectHook:
    """Inject one reflection turn after the agent finishes (no tool calls).

    Implements ``before_turn_end`` — when the agent would exit, this hook
    tells the loop to continue with a user message containing the reflection
    prompt. The final reflection text is available via ``reflection`` after
    the session ends.
    """

    def __init__(self, prompt: str):
        self._prompt = prompt
        self._used = False
        self.reflection: Optional[str] = None

    def before_turn_end(self, message: ChatResponseMessage,
                         ctx: HookContext) -> Optional[BeforeTurnEndResult]:
        if self._used:
            # Second text-only turn — this is the reflection response
            self.reflection = message.content or ""
            logger.info(f"[task-reflect] done, {len(self.reflection)} chars")
            return None
        self._used = True
        logger.info("[task-reflect] starting post-task reflection turn")
        return BeforeTurnEndResult(
            continue_loop=True,
            messages_to_append=[
                ChatMessage(role="user", content=self._prompt),
            ],
        )
