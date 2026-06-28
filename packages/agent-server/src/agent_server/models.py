"""Request and response models for the conversation agent."""

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class ConversationMode(StrEnum):
    CHAT = "chat"
    TEACHER = "teacher"
    PLAY = "play"
    PARENT = "parent"


class ConversationRequest(BaseModel):
    text: str
    conversation_id: str
    language: str = "en"
    source: str = "assist"
    device_id: str | None = None
    satellite_id: str | None = None
    timestamp: datetime = Field(default_factory=datetime.now)


class Action(BaseModel):
    type: str
    target: str | None = None
    data: dict | None = None


class ConversationResponse(BaseModel):
    reply_text: str
    mode: ConversationMode
    continue_conversation: bool = False
    actions: list[Action] = Field(default_factory=list)


class HealthResponse(BaseModel):
    status: str = "ok"
    version: str = "0.1.0"
