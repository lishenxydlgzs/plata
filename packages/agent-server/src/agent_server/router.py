"""Message router — delegates to chat handler with integrated media selection."""

import logging

from .context import ConversationDB
from .media import is_stop_request, media_stop_response
from .models import ConversationRequest, ConversationResponse
from .modes.chat import ChatHandler

logger = logging.getLogger(__name__)


class MessageRouter:
    def __init__(self, conversation_db: ConversationDB) -> None:
        self._db = conversation_db
        self._handler = ChatHandler()

    async def route(self, request: ConversationRequest) -> ConversationResponse:
        if is_stop_request(request.text):
            response = media_stop_response()
        else:
            history = await self._db.get_history(request.conversation_id)
            response = await self._handler.handle(request, history)
        await self._db.save_turn(request.conversation_id, request.text, response.reply_text)
        return response
