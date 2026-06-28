"""Base interface for mode handlers."""

from abc import ABC, abstractmethod

from ..context import ConversationContext
from ..models import ConversationRequest, ConversationResponse


class ModeHandler(ABC):
    @abstractmethod
    async def handle(
        self, request: ConversationRequest, context: ConversationContext
    ) -> ConversationResponse:
        ...
