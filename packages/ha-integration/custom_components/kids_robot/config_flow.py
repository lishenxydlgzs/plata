"""Config flow for Kids Robot integration."""

from __future__ import annotations

import aiohttp
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResult

from .const import (
    CONF_BACKEND_URL,
    CONF_DEFAULT_MODE,
    CONF_MEDIA_PLAYER_ENTITY_ID,
    CONF_TIMEOUT,
    DEFAULT_BACKEND_URL,
    DEFAULT_MEDIA_PLAYER_ENTITY_ID,
    DEFAULT_MODE,
    DEFAULT_TIMEOUT,
    DOMAIN,
)

DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_BACKEND_URL, default=DEFAULT_BACKEND_URL): str,
        vol.Optional(CONF_TIMEOUT, default=DEFAULT_TIMEOUT): int,
        vol.Optional(CONF_DEFAULT_MODE, default=DEFAULT_MODE): vol.In(
            ["chat", "teacher", "play", "parent"]
        ),
        vol.Optional(
            CONF_MEDIA_PLAYER_ENTITY_ID, default=DEFAULT_MEDIA_PLAYER_ENTITY_ID
        ): str,
    }
)


class KidsRobotConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Kids Robot."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict | None = None
    ) -> FlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            url = user_input[CONF_BACKEND_URL].rstrip("/")
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(
                        f"{url}/health", timeout=aiohttp.ClientTimeout(total=5)
                    ) as resp:
                        if resp.status != 200:
                            errors["base"] = "cannot_connect"
            except (aiohttp.ClientError, TimeoutError):
                errors["base"] = "cannot_connect"

            if not errors:
                user_input[CONF_BACKEND_URL] = url
                return self.async_create_entry(
                    title="Kids Robot", data=user_input
                )

        return self.async_show_form(
            step_id="user", data_schema=DATA_SCHEMA, errors=errors
        )
