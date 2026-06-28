"""Parent helper mode - routines, reminders, admin interactions."""

from ..context import ConversationContext
from ..models import ConversationMode, ConversationRequest, ConversationResponse
from .base import ModeHandler


class ParentHandler(ModeHandler):
    async def handle(
        self, request: ConversationRequest, context: ConversationContext
    ) -> ConversationResponse:
        # For M1: acknowledge parent mode entry.
        # Future: routine management, HA action triggers, scheduling.
        return ConversationResponse(
            reply_text="Parent mode active. What would you like to configure?",
            mode=ConversationMode.PARENT,
            continue_conversation=True,
        )
