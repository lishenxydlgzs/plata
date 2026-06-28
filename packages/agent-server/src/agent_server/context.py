"""Conversation context management for multi-turn support."""

from dataclasses import dataclass, field
from datetime import datetime

from .models import ConversationMode


@dataclass
class Turn:
    user_text: str
    reply_text: str
    timestamp: datetime


@dataclass
class ConversationContext:
    conversation_id: str
    mode: ConversationMode = ConversationMode.CHAT
    turns: list[Turn] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.now)

    def add_turn(self, user_text: str, reply_text: str) -> None:
        self.turns.append(Turn(
            user_text=user_text,
            reply_text=reply_text,
            timestamp=datetime.now(),
        ))

    @property
    def last_n_turns(self) -> list[Turn]:
        return self.turns[-10:]


class ContextStore:
    """In-memory conversation context store."""

    def __init__(self) -> None:
        self._contexts: dict[str, ConversationContext] = {}

    def get_or_create(self, conversation_id: str) -> ConversationContext:
        if conversation_id not in self._contexts:
            self._contexts[conversation_id] = ConversationContext(
                conversation_id=conversation_id
            )
        return self._contexts[conversation_id]

    def set_mode(self, conversation_id: str, mode: ConversationMode) -> None:
        ctx = self.get_or_create(conversation_id)
        ctx.mode = mode
