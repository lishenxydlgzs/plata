"""Chat mode handler - general friendly responses."""

from ..context import ConversationContext
from ..models import ConversationMode, ConversationRequest, ConversationResponse
from .base import ModeHandler


class ChatHandler(ModeHandler):
    async def handle(
        self, request: ConversationRequest, context: ConversationContext
    ) -> ConversationResponse:
        # For M1: return a friendly canned response.
        # Future: route to LLM with child-friendly system prompt.
        return ConversationResponse(
            reply_text="Hey there! I'm your robot buddy. What would you like to do today?",
            mode=ConversationMode.CHAT,
            continue_conversation=True,
        )
