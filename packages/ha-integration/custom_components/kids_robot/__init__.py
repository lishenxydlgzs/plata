"""Kids Robot custom conversation agent integration for Home Assistant."""

import asyncio
import logging
from collections.abc import Callable
from datetime import datetime
from uuid import uuid4

import aiohttp

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.event import async_call_later
from homeassistant.core import Event

from .const import (
    CONF_BACKEND_URL,
    CONF_MEDIA_PLAYER_ENTITY_ID,
    DEFAULT_BACKEND_URL,
    DEFAULT_MEDIA_PLAYER_ENTITY_ID,
    DOMAIN,
)

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
    backend_url = entry.data.get(CONF_BACKEND_URL, DEFAULT_BACKEND_URL).rstrip("/")
    active_playlist_task: asyncio.Task | None = None
    active_session_id: str | None = None
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

    async def report_playback(
        session_id: str | None, event: str, track_index: int | None = None
    ) -> None:
        if not session_id:
            return
        payload = {"event": event, "track_index": track_index}
        try:
            timeout = aiohttp.ClientTimeout(total=10)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(
                    f"{backend_url}/playback/sessions/{session_id}/events",
                    json=payload,
                ) as response:
                    if response.status != 200:
                        _LOGGER.warning(
                            "Playback event %s for %s returned %s",
                            event, session_id, response.status,
                        )
        except Exception:
            _LOGGER.exception("Failed to report playback event %s", event)

    async def cancel_active_playlist(final_status: str) -> None:
        nonlocal active_playlist_task, active_session_id
        task = active_playlist_task
        session_id = active_session_id
        if task and task is not asyncio.current_task() and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        active_playlist_task = None
        active_session_id = None
        await hass.services.async_call(
            "media_player",
            "media_stop",
            {"entity_id": media_player_entity_id},
            blocking=True,
        )
        await report_playback(session_id, final_status)

    async def handle_stop_playback(call: ServiceCall) -> None:
        """Stop audio and retain any playlist cursor for later resumption."""
        await cancel_active_playlist("stopped")

    async def handle_play_playlist(call: ServiceCall) -> None:
        """Play a list of tracks sequentially, waiting for each to finish."""
        nonlocal active_playlist_task, active_session_id
        tracks = call.data.get("tracks", [])
        session_id = call.data.get("session_id")
        start_index = call.data.get("start_index", 0)
        if not tracks:
            return
        if not isinstance(start_index, int) or not 0 <= start_index < len(tracks):
            _LOGGER.warning("Ignoring invalid playlist start index: %r", start_index)
            return

        if active_playlist_task and active_playlist_task is not asyncio.current_task():
            await cancel_active_playlist("interrupted")

        current_task = asyncio.current_task()
        active_playlist_task = current_task
        active_session_id = session_id

        _LOGGER.info("Playing playlist: %d tracks, starting at %d", len(tracks), start_index)

        try:
            for i in range(start_index, len(tracks)):
                track = tracks[i]
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

                finished = await _wait_for_playback_complete(hass, media_player_entity_id, timeout=600)
                if not finished:
                    _LOGGER.warning("Playlist: giving up after timeout, stopping at track %d/%d", i + 1, len(tracks))
                    await report_playback(session_id, "failed")
                    break
                await report_playback(session_id, "track_completed", i)
            else:
                _LOGGER.info("Playlist complete")
        except asyncio.CancelledError:
            _LOGGER.info("Playlist interrupted")
            return
        except Exception:
            _LOGGER.exception("Playlist failed during playback")
            await report_playback(session_id, "failed")
        finally:
            if active_playlist_task is current_task:
                active_playlist_task = None
                active_session_id = None

    hass.services.async_register(DOMAIN, "play_playlist", handle_play_playlist)
    hass.services.async_register(DOMAIN, "stop_playback", handle_stop_playback)
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
    hass.services.async_remove(DOMAIN, "stop_playback")
    hass.services.async_remove(DOMAIN, "start_timer")
    return unload_ok
