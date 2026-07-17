"""Media catalog selection and playback response handling."""

import json
import logging
from importlib.resources import files
from typing import Any

from .llm import generate_json
from .models import Action, ConversationMode, ConversationRequest, ConversationResponse

logger = logging.getLogger(__name__)

MEDIA_BASE = "media-source://media_source/local/kids_robot"
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

_catalog_cache: list[dict[str, Any]] | None = None


def get_media_catalog() -> list[dict[str, Any]]:
    """Load configured playable media items."""
    global _catalog_cache
    if _catalog_cache is None:
        catalog_path = files("agent_server").joinpath("media_catalog.json")
        _catalog_cache = json.loads(catalog_path.read_text(encoding="utf-8"))
    return _catalog_cache


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
        searchable = " ".join(
            [item["id"], item["title"], item["description"], *item["aliases"]]
        ).lower()
        for token in text.split():
            if len(token) >= 4 and token in searchable:
                return True

    return False


def _catalog_alias_match(text: str, catalog: list[dict[str, Any]]) -> dict[str, Any] | None:
    for item in catalog:
        names = [item["title"], *item["aliases"]]
        for name in names:
            if name.lower() in text:
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
    if not _looks_like_media_request(text, catalog):
        return None

    direct_match = _catalog_alias_match(text, catalog)
    if direct_match is not None:
        return _media_play_response(direct_match)

    selector_input = json.dumps(
        {
            "user_request": request.text,
            "media_catalog": [
                {
                    "id": item["id"],
                    "title": item["title"],
                    "description": item["description"],
                    "aliases": item["aliases"],
                }
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
