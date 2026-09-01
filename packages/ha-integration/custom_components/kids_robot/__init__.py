"""Kids Robot custom conversation agent integration for Home Assistant."""

import asyncio
import logging
from collections.abc import Callable
from datetime import datetime
from uuid import uuid4

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.event import async_call_later
from homeassistant.core import Event

from .const import CONF_MEDIA_PLAYER_ENTITY_ID, DEFAULT_MEDIA_PLAYER_ENTITY_ID, DOMAIN

_LOGGER = logging.getLogger(__name__)

PLATFORMS = ["conversation"]
TIMER_ALERT_MEDIA = "media-source://media_source/local/kids_robot/timer.wav"


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Kids Robot from a config entry."""
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = entry.data
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    media_player_entity_id = entry.data.get(
        CONF_MEDIA_PLAYER_ENTITY_ID, DEFAULT_MEDIA_PLAYER_ENTITY_ID
    )
    timers: dict[str, Callable[[], None]] = {}
    hass.data[DOMAIN].setdefault("timers", {})[entry.entry_id] = timers

    async def handle_start_timer(call: ServiceCall) -> None:
        """Schedule a timer whose expiry plays the Kids Robot alert sound."""
        duration = call.data.get("duration_seconds")
        if not isinstance(duration, int) or not 1 <= duration <= 24 * 60 * 60:
            _LOGGER.warning("Ignoring invalid timer duration: %r", duration)
            return

        timer_id = str(uuid4())

        async def timer_finished(_: datetime) -> None:
            timers.pop(timer_id, None)
            _LOGGER.info("Timer expired: %s", timer_id)
            player_state = hass.states.get(media_player_entity_id)
            if player_state is None or player_state.state in {"unavailable", "unknown"}:
                _LOGGER.warning(
                    "Timer %s expired but media player %s is %s; alert was not sent",
                    timer_id,
                    media_player_entity_id,
                    player_state.state if player_state else "missing",
                )
                return

            try:
                await hass.services.async_call(
                    "media_player",
                    "play_media",
                    {
                        "entity_id": media_player_entity_id,
                        "media_content_id": TIMER_ALERT_MEDIA,
                        "media_content_type": "music",
                    },
                    blocking=True,
                )
                updated_state = hass.states.get(media_player_entity_id)
                _LOGGER.info(
                    "Timer %s alert request accepted by media player %s (state: %s)",
                    timer_id,
                    media_player_entity_id,
                    updated_state.state if updated_state else "missing",
                )
            except Exception:
                _LOGGER.exception(
                    "Timer %s alert request failed for media player %s",
                    timer_id,
                    media_player_entity_id,
                )

        timers[timer_id] = async_call_later(hass, duration, timer_finished)
        _LOGGER.info("Timer started: %s (%d seconds)", timer_id, duration)

    async def handle_play_playlist(call: ServiceCall) -> None:
        """Play a list of tracks sequentially, waiting for each to finish."""
        tracks = call.data.get("tracks", [])
        if not tracks:
            return

        _LOGGER.info("Playing playlist: %d tracks", len(tracks))

        for i, track in enumerate(tracks):
            _LOGGER.info("Playlist track %d/%d: %s", i + 1, len(tracks), track.split("/")[-1])

            await hass.services.async_call(
                "media_player",
                "play_media",
                {
                    "entity_id": media_player_entity_id,
                    "media_content_id": track,
                    "media_content_type": "music",
                },
                blocking=True,
            )

            # Wait for playback to finish (state goes playing → idle)
            if i < len(tracks) - 1:
                finished = await _wait_for_playback_complete(hass, media_player_entity_id, timeout=600)
                if not finished:
                    _LOGGER.warning("Playlist: giving up after timeout, stopping at track %d/%d", i + 1, len(tracks))
                    break

        _LOGGER.info("Playlist complete")

    hass.services.async_register(DOMAIN, "play_playlist", handle_play_playlist)
    hass.services.async_register(DOMAIN, "start_timer", handle_start_timer)

    return True


async def _wait_for_playback_complete(hass: HomeAssistant, entity_id: str, timeout: int = 600) -> bool:
    """Wait for a media player to finish playing a track.

    The Voice PE has this transition pattern:
      play_media called → playing (brief) → idle (loading) → playing (actual) → idle (done)

    Strategy: wait for stable playing (>3s), then wait for idle/unavailable.
    """
    stable_playing = asyncio.Event()
    finished = asyncio.Event()
    play_start_time: float | None = None

    @callback
    def _state_changed(ev: Event) -> None:
        nonlocal play_start_time
        new_state = ev.data.get("new_state")
        if not new_state:
            return
        state = new_state.state
        if state == "playing":
            if play_start_time is None:
                play_start_time = asyncio.get_event_loop().time()
        elif state in ("idle", "unavailable"):
            if play_start_time is not None:
                elapsed = asyncio.get_event_loop().time() - play_start_time
                if elapsed > 3.0:
                    # Was playing for more than 3s — this is the real finish
                    finished.set()
                else:
                    # Brief playing then idle — just the loading phase, reset
                    play_start_time = None

    unsub = async_track_state_change_event(hass, entity_id, _state_changed)
    try:
        # Give it time to start and finish
        await asyncio.wait_for(finished.wait(), timeout=timeout)
        _LOGGER.info("Playlist: track playback complete")
        # Small delay before starting next track
        await asyncio.sleep(1)
        return True
    except asyncio.TimeoutError:
        _LOGGER.warning("Playlist: timed out waiting for track to finish")
        return False
    finally:
        unsub()


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        timers = hass.data[DOMAIN].get("timers", {}).pop(entry.entry_id, {})
        for cancel in timers.values():
            cancel()
        hass.data[DOMAIN].pop(entry.entry_id)
    hass.services.async_remove(DOMAIN, "play_playlist")
    hass.services.async_remove(DOMAIN, "start_timer")
    return unload_ok
