"""FastAPI application for the kids robot conversation agent."""

import logging

from fastapi import FastAPI, HTTPException

from .context import ContextStore
from .models import (
    ConversationMode,
    ConversationRequest,
    ConversationResponse,
    HealthResponse,
)
from .router import MessageRouter

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

app = FastAPI(title="Kids Robot Agent Server", version="0.1.0")

context_store = ContextStore()
message_router = MessageRouter(context_store)


@app.post("/conversation", response_model=ConversationResponse)
async def conversation(request: ConversationRequest) -> ConversationResponse:
    logger.info(
        "Incoming: text=%r conversation_id=%s mode=%s",
        request.text,
        request.conversation_id,
        context_store.get_or_create(request.conversation_id).mode,
    )
    try:
        response = await message_router.route(request)
    except Exception:
        logger.exception("Error processing conversation request")
        response = ConversationResponse(
            reply_text="Oops, something went wrong. Let me try again in a moment.",
            mode=ConversationMode.CHAT,
            continue_conversation=False,
        )
    logger.info("Reply: text=%r mode=%s", response.reply_text, response.mode)
    return response


@app.post("/mode/{mode_name}/start", response_model=ConversationResponse)
async def start_mode(mode_name: str, request: ConversationRequest) -> ConversationResponse:
    try:
        mode = ConversationMode(mode_name)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Unknown mode: {mode_name}")
    context_store.set_mode(request.conversation_id, mode)
    return await message_router.route(request)


@app.post("/hardware/button", response_model=ConversationResponse)
async def hardware_button(request: ConversationRequest) -> ConversationResponse:
    logger.info("Hardware button event: conversation_id=%s", request.conversation_id)
    return await message_router.route(request)


@app.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse()


@app.get("/status")
async def status() -> dict:
    return {
        "status": "running",
        "active_conversations": len(context_store._contexts),
    }
