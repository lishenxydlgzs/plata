"""Media catalog scanning and action helpers."""

import logging
import os
from pathlib import Path
from typing import Any

from .models import Action, ConversationMode, ConversationResponse

logger = logging.getLogger(__name__)

MEDIA_DIR = Path(os.environ.get(
    "MEDIA_DIR", "/home/lishenxydlgzs/homeassistant/media/kids_robot"
))
MEDIA_BASE = "media-source://media_source/local/kids_robot"
MEDIA_EXTENSIONS = {".mp3", ".mp4", ".wav", ".ogg", ".flac", ".m4a"}
STOP_WORDS = ("stop", "pause", "quiet")
MEDIA_WORDS = ("audio", "music", "song", "sound", "story")


def _title_from_filename(stem: str) -> str:
    """Convert a filename stem like 'bedtime_music' or 'my-lullaby' to a title."""
    return stem.replace("_", " ").replace("-", " ").title()


def scan_media_catalog() -> list[dict[str, Any]]:
    """Scan the media directory and build a catalog from filenames."""
    if not MEDIA_DIR.is_dir():
        logger.warning("Media directory does not exist: %s", MEDIA_DIR)
        return []

    catalog = []
    for path in sorted(MEDIA_DIR.iterdir()):
        if not path.is_file() or path.suffix.lower() not in MEDIA_EXTENSIONS:
            continue
        stem = path.stem
        title = _title_from_filename(stem)
        catalog.append({
            "id": stem.lower().replace("-", "_").replace(" ", "_"),
            "title": title,
            "file": path.name,
            "media_content_type": "music",
        })

    logger.info("Media catalog scanned: %d items from %s", len(catalog), MEDIA_DIR)
    return catalog


def get_media_catalog() -> list[dict[str, Any]]:
    """Return the current media catalog by scanning the media directory."""
    return scan_media_catalog()


def is_stop_request(text: str) -> bool:
    """Check if the user wants to stop/pause audio."""
    lower = text.lower()
    return any(w in lower for w in STOP_WORDS) and any(w in lower for w in MEDIA_WORDS)


def media_stop_response() -> ConversationResponse:
    return ConversationResponse(
        reply_text="Okay, I'll stop the audio.",
        mode=ConversationMode.CHAT,
        continue_conversation=False,
        actions=[
            Action(
                type="ha_service",
                data={
                    "domain": "media_player",
                    "service": "media_stop",
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
