"""FastAPI application for the kids robot conversation agent."""

import logging
import os
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI

from contextlib import asynccontextmanager

from .context import ConversationDB
from .models import (
    ConversationMode,
    ConversationRequest,
    ConversationResponse,
    HealthResponse,
)
from .router import MessageRouter

LOG_DIR = Path(os.environ.get("LOG_DIR", "/home/lishenxydlgzs/logs/agent-server"))

handlers: list[logging.Handler] = [logging.StreamHandler()]
try:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    handlers.append(
        TimedRotatingFileHandler(
            LOG_DIR / "agent-server.log",
            when="midnight",
            backupCount=7,
        )
    )
except OSError:
    pass

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=handlers,
)
logger = logging.getLogger(__name__)

conversation_db = ConversationDB()
message_router = MessageRouter(conversation_db)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await conversation_db.connect()
    yield
    await conversation_db.close()


app = FastAPI(title="Kids Robot Agent Server", version="0.1.0", lifespan=lifespan)


@app.post("/conversation", response_model=ConversationResponse)
async def conversation(request: ConversationRequest) -> ConversationResponse:
    logger.info(
        "Incoming: text=%r conversation_id=%s",
        request.text,
        request.conversation_id,
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
    logger.info("Reply: text=%r", response.reply_text)
    return response


@app.post("/hardware/button", response_model=ConversationResponse)
async def hardware_button(request: ConversationRequest) -> ConversationResponse:
    logger.info("Hardware button event: conversation_id=%s", request.conversation_id)
    return await message_router.route(request)


@app.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse()


@app.get("/status")
async def status() -> dict:
    return {"status": "running"}
