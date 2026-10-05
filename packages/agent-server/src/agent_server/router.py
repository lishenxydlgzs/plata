"""Message router — delegates to chat handler with integrated media selection."""

import logging

from .context import ConversationDB
from .knowledge import KnowledgeStore
from .models import ConversationRequest, ConversationResponse
from .modes.chat import ChatHandler

logger = logging.getLogger(__name__)


class MessageRouter:
    def __init__(self, conversation_db: ConversationDB, knowledge: KnowledgeStore) -> None:
        self._db = conversation_db
        self._knowledge = knowledge
        self._handler = ChatHandler(knowledge)

    async def route(self, request: ConversationRequest) -> ConversationResponse:
        history = await self._db.get_history(request.conversation_id)
        response = await self._handler.handle(request, history)
        await self._db.save_turn(request.conversation_id, request.text, response.reply_text)
        return response
