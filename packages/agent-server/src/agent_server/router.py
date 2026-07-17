"""Message router — single-mode, delegates to LLM for context-aware responses."""

import logging

from .context import ConversationDB
from .media import media_response_for
from .models import ConversationRequest, ConversationResponse
from .modes.chat import ChatHandler

logger = logging.getLogger(__name__)


class MessageRouter:
    def __init__(self, conversation_db: ConversationDB) -> None:
        self._db = conversation_db
        self._handler = ChatHandler()

    async def route(self, request: ConversationRequest) -> ConversationResponse:
        history = await self._db.get_history(request.conversation_id)
        response = await media_response_for(request)
        if response is None:
            response = await self._handler.handle(request, history)
        await self._db.save_turn(request.conversation_id, request.text, response.reply_text)
        return response
