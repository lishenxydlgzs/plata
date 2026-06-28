"""Play mode handler - role-play, storytelling, creative prompts."""

from ..context import ConversationContext
from ..models import ConversationMode, ConversationRequest, ConversationResponse
from .base import ModeHandler


class PlayHandler(ModeHandler):
    async def handle(
        self, request: ConversationRequest, context: ConversationContext
    ) -> ConversationResponse:
        # For M1: acknowledge play mode entry.
        # Future: story engine, character persistence, creative prompts.
        return ConversationResponse(
            reply_text="Ooh, let's play! Should we go on an adventure, or would you like to pretend to be something?",
            mode=ConversationMode.PLAY,
            continue_conversation=True,
        )
