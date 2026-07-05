"""Base interface for mode handlers."""

import logging
from abc import ABC, abstractmethod

from ..models import ConversationMode, ConversationRequest, ConversationResponse

logger = logging.getLogger(__name__)

FALLBACK_REPLY = "Hmm, my brain is a little fuzzy right now. Can you try again?"


class ModeHandler(ABC):
    mode: ConversationMode

    @abstractmethod
    async def _generate(
        self, request: ConversationRequest, history: list[dict]
    ) -> str:
        ...

    async def handle(
        self, request: ConversationRequest, history: list[dict]
    ) -> ConversationResponse:
        try:
            reply = await self._generate(request, history)
        except Exception:
            logger.exception("LLM generation failed in %s mode", self.mode)
            reply = FALLBACK_REPLY

        return ConversationResponse(
            reply_text=reply,
            mode=self.mode,
            continue_conversation=True,
        )
