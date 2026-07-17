"""LLM client for Gemini Flash."""

import json
import logging
import os

from google import genai
from google.genai import types

logger = logging.getLogger(__name__)

_client: genai.Client | None = None


def get_client() -> genai.Client:
    global _client
    if _client is None:
        api_key = os.environ.get("GOOGLE_AI_STUDIO_API_KEY")
        if not api_key:
            raise RuntimeError("GOOGLE_AI_STUDIO_API_KEY not set")
        _client = genai.Client(api_key=api_key)
    return _client


async def generate(system_prompt: str, conversation_history: list[dict], user_text: str) -> str:
    """Generate a response using Gemini Flash.

    Args:
        system_prompt: The system instruction for this mode.
        conversation_history: List of {"role": "user"|"model", "text": "..."} dicts.
        user_text: The current user message.
    """
    client = get_client()

    contents = []
    for turn in conversation_history:
        contents.append(types.Content(
            role=turn["role"],
            parts=[types.Part.from_text(text=turn["text"])],
        ))
    contents.append(types.Content(
        role="user",
        parts=[types.Part.from_text(text=user_text)],
    ))

    try:
        response = await client.aio.models.generate_content(
            model="gemini-3.1-flash-lite",
            contents=contents,
            config=types.GenerateContentConfig(
                system_instruction=system_prompt,
                max_output_tokens=80,
                temperature=0.7,
            ),
        )
        text = response.text
        if not text:
            return ""
        return text.strip()
    except Exception:
        logger.exception("Gemini API call failed")
        raise


async def generate_json(system_prompt: str, user_text: str) -> dict:
    """Generate a small JSON object using Gemini Flash."""
    client = get_client()

    try:
        response = await client.aio.models.generate_content(
            model="gemini-3.1-flash-lite",
            contents=[
                types.Content(
                    role="user",
                    parts=[types.Part.from_text(text=user_text)],
                )
            ],
            config=types.GenerateContentConfig(
                system_instruction=system_prompt,
                max_output_tokens=120,
                temperature=0,
                response_mime_type="application/json",
            ),
        )
        text = response.text
        if not text:
            return {}
        return json.loads(text)
    except Exception:
        logger.exception("Gemini JSON API call failed")
        raise
