"""Chat handler - single mode with integrated media selection and topic extraction."""

import logging

from ..knowledge import KnowledgeStore
from ..llm import generate_chat_json
from ..media import get_media_catalog, get_playlist_catalog, resolve_playlist, media_play_response, media_playlist_response
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

{memory_context}

You have a music library with individual songs and playlists. \
When the user asks to play, hear, or listen to something, \
pick the best match(es) from the lists below. \
If they ask for a single song, use media_ids with one item. \
If they ask for a playlist by name (e.g. "play CC week 5"), use the playlist ID. \
If they ask for multiple songs (e.g. "play some bedtime music", \
"play a few songs"), pick 3-8 good matches from individual songs. \
If no good match exists or the user isn't asking for media, set media_ids to an empty list.

Available media:
{media_list}

Respond ONLY with JSON in this exact shape:
{{"reply_text": "your spoken reply here", "media_ids": ["id1", "id2"], "topics": ["topic1", "topic2"], "facts": []}}

If media_ids has items, reply_text should tell the child what you're about to play. \
If media_ids is empty, reply_text is your normal conversational response.

topics: list 0-3 key subjects or interests the user expressed in this message \
(e.g. "dinosaurs", "space", "bedtime"). Only include clear topics, not filler. \
Empty list if the message is just a greeting or has no clear topic.

facts: list 0-2 factual statements the user is telling you about themselves, their family, \
or their world. Only include things the user EXPLICITLY stated as true — \
NOT things you inferred or guessed. Each fact has subject, relation, object, and your \
confidence (0.0-1.0) that the user actually stated this. \
Format: {{"subject": "Pang Pang", "relation": "is_a", "object": "family dog", "confidence": 0.9}}

Examples of GOOD fact extractions:
- "My dog's name is Pang Pang" → {{"subject": "Pang Pang", "relation": "is_a", "object": "family dog", "confidence": 0.9}}
- "I'm in first grade" → {{"subject": "speaker", "relation": "is_in", "object": "first grade", "confidence": 0.9}}
- "We do CC on Tuesdays" → {{"subject": "CC", "relation": "happens_on", "object": "Tuesdays", "confidence": 0.8}}

Do NOT extract facts when:
- User asks to play music (not a fact about them)
- User mentions a topic in passing (not an explicit statement)
- You are guessing or inferring something not directly said
Empty list for most messages.\
"""

FALLBACK_REPLY = "Hmm, my brain is a little fuzzy right now. Can you try again?"


def _build_system_prompt(knowledge: KnowledgeStore) -> str:
    catalog = get_media_catalog()
    if catalog:
        media_list = "\n".join(f"- {item['id']}" for item in catalog)
    else:
        media_list = "(no media files available)"

    playlists = get_playlist_catalog()
    if playlists:
        playlist_list = "\n".join(f"- {pid}" for pid in sorted(playlists.keys()))
        media_list += f"\n\nAvailable playlists (plays all songs in order):\n{playlist_list}"
        media_list += "\n\nTo play a specific subject from a playlist, append the subject: " \
                      "e.g. cc_cycle3_week_5_science, cc_cycle3_week_3_bible, cc_cycle3_week_1_math. " \
                      "Subjects: bible, english, history, science, latin, geography, timeline, math."

    memory_context = knowledge.build_memory_prompt()

    return SYSTEM_PROMPT.format(media_list=media_list, memory_context=memory_context)


class ChatHandler:
    def __init__(self, knowledge: KnowledgeStore) -> None:
        self._knowledge = knowledge

    async def handle(
        self, request: ConversationRequest, history: list[dict]
    ) -> ConversationResponse:
        system_prompt = _build_system_prompt(self._knowledge)
        memory_context = self._knowledge.build_memory_prompt()
        logger.info(
            "Context: history_turns=%d memory=%r user_text=%r",
            len(history), memory_context, request.text,
        )

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
        media_ids = result.get("media_ids") or []
        # Backwards compat: handle old single media_id format
        if not media_ids and result.get("media_id"):
            media_ids = [result["media_id"]]
        topics = result.get("topics") or []

        facts_raw = result.get("facts") or []
        logger.info("LLM result: media_ids=%s topics=%r facts=%r", media_ids, topics, facts_raw)

        # Resolve media IDs against catalog and playlists
        catalog = get_media_catalog()
        catalog_by_id = {item["id"]: item for item in catalog}
        resolved_items: list[dict] = []
        is_playlist = False
        for mid in media_ids:
            # Check if it's a playlist ID
            playlist_tracks = resolve_playlist(mid)
            if playlist_tracks:
                resolved_items = [{"file": t["file"], "media_content_type": "music", "id": mid} for t in playlist_tracks]
                is_playlist = True
                logger.info("Resolved playlist %s: %d tracks", mid, len(resolved_items))
                break
            elif mid in catalog_by_id:
                resolved_items.append(catalog_by_id[mid])
            else:
                logger.warning("LLM returned unknown media_id: %s", mid)

        # Record message in knowledge graph
        first_media_id = resolved_items[0]["id"] if resolved_items else None
        facts = result.get("facts") or []
        try:
            msg_id = self._knowledge.record_message(
                text=request.text,
                conversation_id=request.conversation_id,
                topics=topics,
                media_id=first_media_id,
            )
            if facts:
                self._knowledge.record_facts(facts, msg_id)
        except Exception:
            logger.exception("Failed to record message in knowledge graph")

        # Return appropriate response
        if is_playlist or len(resolved_items) > 1:
            return media_playlist_response(reply_text, resolved_items)
        elif len(resolved_items) == 1:
            return media_play_response(reply_text, resolved_items[0])

        return ConversationResponse(
            reply_text=reply_text,
            mode=ConversationMode.CHAT,
            continue_conversation=True,
        )
