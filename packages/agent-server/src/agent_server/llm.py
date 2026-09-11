"""LLM client for Gemini Flash."""

import asyncio
import json
import logging
import os

from google import genai
from google.genai import types

logger = logging.getLogger(__name__)

_client: genai.Client | None = None
DEFAULT_MODELS = (
    "gemini-3.5-flash-lite",
    "gemini-3.1-flash-lite",
    "gemini-2.5-flash-lite",
)
FALLBACK_STATUS_CODES = {404, 429, 500, 502, 503, 504}
DEFAULT_MODEL_TIMEOUT_SECONDS = 8.0


def get_client() -> genai.Client:
    global _client
    if _client is None:
        api_key = os.environ.get("GOOGLE_AI_STUDIO_API_KEY")
        if not api_key:
            raise RuntimeError("GOOGLE_AI_STUDIO_API_KEY not set")
        _client = genai.Client(api_key=api_key)
    return _client


def get_models() -> tuple[str, ...]:
    """Return the configured model preference order."""
    configured = os.environ.get("GEMINI_MODELS")
    if not configured:
        return DEFAULT_MODELS
    models = tuple(model.strip() for model in configured.split(",") if model.strip())
    return models or DEFAULT_MODELS


def get_model_timeout_seconds() -> float:
    """Return the maximum wall time for one model attempt."""
    configured = os.environ.get("GEMINI_MODEL_TIMEOUT_SECONDS")
    if not configured:
        return DEFAULT_MODEL_TIMEOUT_SECONDS
    try:
        return max(1.0, float(configured))
    except ValueError:
        logger.warning(
            "Invalid GEMINI_MODEL_TIMEOUT_SECONDS=%r; using %.1f",
            configured, DEFAULT_MODEL_TIMEOUT_SECONDS,
        )
        return DEFAULT_MODEL_TIMEOUT_SECONDS


def _is_fallback_error(error: Exception) -> bool:
    """Whether an API error should move generation to the next model."""
    code = getattr(error, "code", None)
    if code is None:
        response = getattr(error, "response", None)
        code = getattr(response, "status_code", None)
    try:
        return int(code) in FALLBACK_STATUS_CODES
    except (TypeError, ValueError):
        return False


async def generate_content_with_fallback(
    contents: list[types.Content], config: types.GenerateContentConfig
):
    """Generate content, moving to another configured model on temporary failures."""
    client = get_client()
    models = get_models()
    timeout = get_model_timeout_seconds()
    for index, model in enumerate(models):
        try:
            return await asyncio.wait_for(
                client.aio.models.generate_content(
                    model=model, contents=contents, config=config
                ),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            if index < len(models) - 1:
                logger.warning(
                    "Gemini model %s exceeded %.1fs; trying %s",
                    model, timeout, models[index + 1],
                )
                continue
            raise
        except Exception as error:
            if _is_fallback_error(error) and index < len(models) - 1:
                logger.warning(
                    "Gemini model %s unavailable (%s); trying %s",
                    model, getattr(error, "code", "unknown"), models[index + 1],
                )
                continue
            raise
    raise RuntimeError("No Gemini models configured")


async def generate(system_prompt: str, conversation_history: list[dict], user_text: str) -> str:
    """Generate a text response using Gemini Flash."""
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
        response = await generate_content_with_fallback(
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


async def generate_chat_json(
    system_prompt: str, conversation_history: list[dict], user_text: str
) -> dict:
    """Generate a structured JSON response with conversation history."""
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
        response = await generate_content_with_fallback(
            contents=contents,
            config=types.GenerateContentConfig(
                system_instruction=system_prompt,
                # Allow the spoken reply plus bounded learning/behavior records.
                max_output_tokens=1536,
                temperature=0.7,
                response_mime_type="application/json",
            ),
        )
        text = response.text
        if not text:
            return {}
        return json.loads(text)
    except Exception:
        logger.exception("Gemini chat JSON API call failed")
        raise


async def generate_json(system_prompt: str, user_text: str) -> dict:
    """Generate a small JSON object using Gemini Flash."""
    try:
        response = await generate_content_with_fallback(
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
