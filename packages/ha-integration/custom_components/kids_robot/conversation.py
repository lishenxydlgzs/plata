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
    CONF_MEDIA_PLAYER_ENTITY_ID,
    CONF_TIMEOUT,
    DEFAULT_BACKEND_URL,
    DEFAULT_MEDIA_PLAYER_ENTITY_ID,
    DEFAULT_TIMEOUT,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

FALLBACK_RESPONSE = "Oops, I can't think right now. Try again in a moment!"
ALLOWED_MEDIA_PLAYER_SERVICES = {
    "play_media",
    "media_stop",
    "media_pause",
    "media_play",
}


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
        self._media_player_entity_id = config_entry.data.get(
            CONF_MEDIA_PLAYER_ENTITY_ID, DEFAULT_MEDIA_PLAYER_ENTITY_ID
        )

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
            backend_response = await self._call_backend(payload)
        except Exception:
            _LOGGER.exception("Failed to get response from agent server")
            backend_response = {"reply_text": FALLBACK_RESPONSE, "actions": []}

        reply_text = backend_response.get("reply_text", FALLBACK_RESPONSE)
        await self._execute_actions(backend_response.get("actions", []))

        response = IntentResponse(language=user_input.language or "en")
        response.async_set_speech(reply_text)

        return ConversationResult(
            response=response,
            conversation_id=user_input.conversation_id,
        )

    async def _call_backend(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Call the agent server and return the response body."""
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
                    return {"reply_text": FALLBACK_RESPONSE, "actions": []}

                return await resp.json()

    async def _execute_actions(self, actions: list[dict[str, Any]]) -> None:
        """Execute supported Home Assistant actions from the agent server."""
        for action in actions:
            if action.get("type") != "ha_service":
                _LOGGER.warning("Ignoring unsupported action type: %s", action)
                continue

            data = action.get("data") or {}
            domain = data.get("domain")
            service = data.get("service")
            if (
                domain != "media_player"
                or service not in ALLOWED_MEDIA_PLAYER_SERVICES
            ):
                _LOGGER.warning("Ignoring unsupported HA service action: %s", action)
                continue

            service_data = dict(data.get("service_data") or {})
            service_data.setdefault(
                "entity_id", action.get("target") or self._media_player_entity_id
            )

            try:
                await self.hass.services.async_call(
                    domain,
                    service,
                    service_data,
                    blocking=False,
                )
            except Exception:
                _LOGGER.exception("Failed to execute HA service action: %s", action)
