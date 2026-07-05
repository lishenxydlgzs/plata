"""Conversation entity for Kids Robot agent."""

from __future__ import annotations

import logging
from typing import Any

import aiohttp

from homeassistant.components.conversation import (
    AbstractConversationAgent,
    ConversationEntity,
    ConversationInput,
    ConversationResult,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.intent import IntentResponse

from .const import (
    CONF_BACKEND_URL,
    CONF_TIMEOUT,
    DEFAULT_BACKEND_URL,
    DEFAULT_TIMEOUT,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

FALLBACK_RESPONSE = "Oops, I can't think right now. Try again in a moment!"


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the conversation entity."""
    async_add_entities([KidsRobotConversationEntity(config_entry)])


class KidsRobotConversationEntity(ConversationEntity):
    """Conversation entity that bridges to the Kids Robot agent server."""

    _attr_has_entity_name = True
    _attr_name = "Kids Robot"

    def __init__(self, config_entry: ConfigEntry) -> None:
        """Initialize."""
        self._config_entry = config_entry
        self._attr_unique_id = config_entry.entry_id
        self._backend_url = config_entry.data.get(
            CONF_BACKEND_URL, DEFAULT_BACKEND_URL
        )
        self._timeout = config_entry.data.get(CONF_TIMEOUT, DEFAULT_TIMEOUT)

    @property
    def supported_languages(self) -> list[str]:
        """Return supported languages."""
        return ["en"]

    async def async_process(
        self, user_input: ConversationInput
    ) -> ConversationResult:
        """Process a conversation turn."""
        payload = {
            "text": user_input.text,
            "conversation_id": user_input.conversation_id or "",
            "language": user_input.language or "en",
            "source": "assist",
            "device_id": user_input.device_id,
        }

        try:
            reply_text = await self._call_backend(payload)
        except Exception:
            _LOGGER.exception("Failed to get response from agent server")
            reply_text = FALLBACK_RESPONSE

        response = IntentResponse(language=user_input.language or "en")
        response.async_set_speech(reply_text)

        return ConversationResult(
            response=response,
            conversation_id=user_input.conversation_id,
        )

    async def _call_backend(self, payload: dict[str, Any]) -> str:
        """Call the agent server and return the reply text."""
        url = f"{self._backend_url}/conversation"
        timeout = aiohttp.ClientTimeout(total=self._timeout)

        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(url, json=payload) as resp:
                if resp.status != 200:
                    _LOGGER.error(
                        "Agent server returned %s: %s",
                        resp.status,
                        await resp.text(),
                    )
                    return FALLBACK_RESPONSE

                data = await resp.json()
                return data.get("reply_text", FALLBACK_RESPONSE)
