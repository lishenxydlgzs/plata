"""Media catalog selection and playback response handling."""

import json
import logging
import os
from pathlib import Path
from typing import Any

from .llm import generate_json
from .models import Action, ConversationMode, ConversationRequest, ConversationResponse

logger = logging.getLogger(__name__)

MEDIA_DIR = Path(os.environ.get(
    "MEDIA_DIR", "/home/lishenxydlgzs/homeassistant/media/kids_robot"
))
MEDIA_BASE = "media-source://media_source/local/kids_robot"
MEDIA_EXTENSIONS = {".mp3", ".mp4", ".wav", ".ogg", ".flac", ".m4a"}
STOP_WORDS = ("stop", "pause", "quiet")
MEDIA_WORDS = (
    "audio",
    "bedtime",
    "hear",
    "listen",
    "lullaby",
    "music",
    "play",
    "song",
    "sound",
    "story",
)

MEDIA_SELECTOR_PROMPT = """\
You select local audio for a child-safe robot assistant.
Given a user request and a JSON media catalog, return only JSON in this shape:
{"media_id": string|null, "reason": string}

Choose a media_id only when the user is asking to play, hear, listen to, start, or have an existing recording/audio/story/music played.
Return null when the request is not about playing local media, when the request is ambiguous, or when no catalog item matches.
Prefer the most specific matching title or alias. Never invent media ids.
"""


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


def _media_stop_response() -> ConversationResponse:
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


def _media_play_response(item: dict[str, Any]) -> ConversationResponse:
    return ConversationResponse(
        reply_text=f"Okay, I'll play {item['title']}.",
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


def _looks_like_media_request(text: str, catalog: list[dict[str, Any]]) -> bool:
    if any(word in text for word in MEDIA_WORDS):
        return True

    for item in catalog:
        searchable = f"{item['id']} {item['title']}".lower()
        for token in text.split():
            if len(token) >= 4 and token in searchable:
                return True

    return False


def _catalog_title_match(text: str, catalog: list[dict[str, Any]]) -> dict[str, Any] | None:
    for item in catalog:
        if item["title"].lower() in text:
            return item

    return None


async def media_response_for(request: ConversationRequest) -> ConversationResponse | None:
    """Return a media action response when the user asks for playable audio."""
    text = request.text.lower()

    if any(word in text for word in STOP_WORDS) and any(
        word in text for word in MEDIA_WORDS
    ):
        return _media_stop_response()

    catalog = get_media_catalog()
    if not catalog or not _looks_like_media_request(text, catalog):
        return None

    direct_match = _catalog_title_match(text, catalog)
    if direct_match is not None:
        return _media_play_response(direct_match)

    selector_input = json.dumps(
        {
            "user_request": request.text,
            "media_catalog": [
                {"id": item["id"], "title": item["title"]}
                for item in catalog
            ],
        }
    )

    try:
        selection = await generate_json(MEDIA_SELECTOR_PROMPT, selector_input)
    except Exception:
        return None

    media_id = selection.get("media_id")
    if media_id is None:
        return None

    for item in catalog:
        if item["id"] == media_id:
            return _media_play_response(item)

    logger.warning("Media selector returned unknown media_id: %s", media_id)
    return None
