"""Mode-aware message router.

Routes incoming messages to the appropriate handler based on
conversation mode and message content.
"""

import logging
import re

from .context import ContextStore, ConversationContext
from .models import ConversationMode, ConversationRequest, ConversationResponse
from .modes.base import ModeHandler
from .modes.chat import ChatHandler
from .modes.teacher import TeacherHandler
from .modes.play import PlayHandler
from .modes.parent import ParentHandler

logger = logging.getLogger(__name__)

MODE_SWITCH_PATTERNS: list[tuple[re.Pattern, ConversationMode]] = [
    (re.compile(r"\b(quiz|teach|lesson|study|learn)\b", re.IGNORECASE), ConversationMode.TEACHER),
    (re.compile(r"\b(play|pretend|story|imagine|adventure)\b", re.IGNORECASE), ConversationMode.PLAY),
    (re.compile(r"\b(parent mode|admin|routine|schedule)\b", re.IGNORECASE), ConversationMode.PARENT),
    (re.compile(r"\b(chat|talk|hello|hi|hey)\b", re.IGNORECASE), ConversationMode.CHAT),
]


class MessageRouter:
    def __init__(self, context_store: ContextStore) -> None:
        self._context_store = context_store
        self._handlers: dict[ConversationMode, ModeHandler] = {
            ConversationMode.CHAT: ChatHandler(),
            ConversationMode.TEACHER: TeacherHandler(),
            ConversationMode.PLAY: PlayHandler(),
            ConversationMode.PARENT: ParentHandler(),
        }

    async def route(self, request: ConversationRequest) -> ConversationResponse:
        ctx = self._context_store.get_or_create(request.conversation_id)
        detected_mode = self._detect_mode_switch(request.text)

        if detected_mode and detected_mode != ctx.mode:
            logger.info(
                "Mode switch: %s -> %s (conversation=%s)",
                ctx.mode, detected_mode, request.conversation_id,
            )
            ctx.mode = detected_mode

        handler = self._handlers[ctx.mode]
        response = await handler.handle(request, ctx)

        ctx.add_turn(request.text, response.reply_text)
        return response

    def _detect_mode_switch(self, text: str) -> ConversationMode | None:
        for pattern, mode in MODE_SWITCH_PATTERNS:
            if pattern.search(text):
                return mode
        return None
