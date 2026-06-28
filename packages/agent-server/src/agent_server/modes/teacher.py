"""Teacher mode handler - quiz, recitation, step-by-step teaching."""

from ..context import ConversationContext
from ..models import ConversationMode, ConversationRequest, ConversationResponse
from .base import ModeHandler


class TeacherHandler(ModeHandler):
    async def handle(
        self, request: ConversationRequest, context: ConversationContext
    ) -> ConversationResponse:
        # For M1: acknowledge teacher mode entry.
        # Future: curriculum-aware lesson logic, quiz state machine.
        return ConversationResponse(
            reply_text="Okay, let's learn something! What subject would you like to work on?",
            mode=ConversationMode.TEACHER,
            continue_conversation=True,
        )
