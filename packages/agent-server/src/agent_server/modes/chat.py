"""Chat handler - single mode with integrated media selection."""

import logging

from ..llm import generate_chat_json
from ..media import get_media_catalog, media_play_response
from ..models import ConversationMode, ConversationRequest, ConversationResponse

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
Your name is Plata. You are a friendly robot companion for a family with young children (ages 4-10). \
You live in their home and are always ready to chat, teach, play, or help. \
Adapt your tone and approach based on what the child or parent is asking for — \
if they want to learn, teach patiently; if they want to play pretend, be imaginative; \
if they ask a question, answer simply and warmly. \
Keep replies to 1-3 short sentences. \
Speak naturally as if talking out loud — no lists, no bullet points, no markdown. \
Never say anything scary, mean, or inappropriate for children. \
If a parent is clearly speaking (asking about routines, schedules, configuration), \
be direct and practical. \
If you don't know something, say so cheerfully.

You have a music library. When the user asks to play, hear, or listen to something, \
pick the best match from the list below. If no good match exists or the user isn't asking for media, \
set media_id to null.

Available media:
{media_list}

Respond ONLY with JSON in this exact shape:
{{"reply_text": "your spoken reply here", "media_id": "id_from_list_or_null"}}

If you pick a media_id, reply_text should tell the child what you're about to play. \
If media_id is null, reply_text is your normal conversational response.\
"""

FALLBACK_REPLY = "Hmm, my brain is a little fuzzy right now. Can you try again?"


def _build_system_prompt() -> str:
    catalog = get_media_catalog()
    if catalog:
        media_list = "\n".join(f"- {item['id']}" for item in catalog)
    else:
        media_list = "(no media files available)"
    return SYSTEM_PROMPT.format(media_list=media_list)


class ChatHandler:
    async def handle(
        self, request: ConversationRequest, history: list[dict]
    ) -> ConversationResponse:
        system_prompt = _build_system_prompt()

        try:
            result = await generate_chat_json(system_prompt, history, request.text)
        except Exception:
            logger.exception("LLM generation failed")
            return ConversationResponse(
                reply_text=FALLBACK_REPLY,
                mode=ConversationMode.CHAT,
                continue_conversation=True,
            )

        reply_text = result.get("reply_text") or FALLBACK_REPLY
        media_id = result.get("media_id")

        if media_id:
            catalog = get_media_catalog()
            for item in catalog:
                if item["id"] == media_id:
                    return media_play_response(reply_text, item)
            logger.warning("LLM returned unknown media_id: %s", media_id)

        return ConversationResponse(
            reply_text=reply_text,
            mode=ConversationMode.CHAT,
            continue_conversation=True,
        )
