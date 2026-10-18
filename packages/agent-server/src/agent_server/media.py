"""Local media file validation and Home Assistant action formatting."""

import logging
import os
from pathlib import Path
from typing import Any

from .models import Action, ConversationMode, ConversationResponse

logger = logging.getLogger(__name__)

MEDIA_DIR = Path(os.environ.get("MEDIA_DIR", "./media"))
MEDIA_BASE = "media-source://media_source/local/kids_robot"
MEDIA_EXTENSIONS = {".mp3", ".mp4", ".wav", ".ogg", ".flac", ".m4a"}
SYSTEM_MEDIA_FILENAMES = {"timer.wav"}

def _title_from_filename(stem: str) -> str:
    """Convert a filename stem like 'bedtime_music' or 'my-lullaby' to a title."""
    return stem.replace("_", " ").replace("-", " ").title()


def _is_playable_file(path: Path) -> bool:
    """Return whether Home Assistant can safely resolve this media path."""
    if not path.is_file() or path.suffix.lower() not in MEDIA_EXTENSIONS:
        return False
    if ".." in path.name:
        logger.warning("Skipping unsafe media filename containing '..': %s", path)
        return False
    return True


def media_stop_response(reply_text: str) -> ConversationResponse:
    return ConversationResponse(
        reply_text=reply_text,
        mode=ConversationMode.CHAT,
        continue_conversation=False,
        actions=[
            Action(
                type="ha_service",
                data={
                    "domain": "kids_robot",
                    "service": "stop_playback",
                    "service_data": {},
                },
            )
        ],
    )


def media_play_response(reply_text: str, item: dict[str, Any]) -> ConversationResponse:
    return ConversationResponse(
        reply_text=reply_text,
        mode=ConversationMode.CHAT,
        continue_conversation=False,
        actions=[
            Action(
                type="ha_service",
                data={
                    "domain": "media_player",
                    "service": "play_media",
                    "service_data": {
                        "media_content_id": f"{MEDIA_BASE}/{item['file']}",
                        "media_content_type": item["media_content_type"],
                    },
                },
            )
        ],
    )


def media_playlist_response(
    reply_text: str,
    items: list[dict[str, Any]],
    session_id: str | None = None,
    start_index: int = 0,
) -> ConversationResponse:
    """Build a response that plays multiple tracks sequentially."""
    tracks = [f"{MEDIA_BASE}/{item['file']}" for item in items]
    return ConversationResponse(
        reply_text=reply_text,
        mode=ConversationMode.CHAT,
        continue_conversation=False,
        actions=[
            Action(
                type="ha_service",
                data={
                    "domain": "kids_robot",
                    "service": "play_playlist",
                    "service_data": {
                        "tracks": tracks,
                        "session_id": session_id,
                        "start_index": start_index,
                    },
                },
            )
        ],
    )
