"""FastAPI application for the kids robot conversation agent."""

import logging
import os
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse

from contextlib import asynccontextmanager

from .context import ConversationDB
from .knowledge import KnowledgeStore
from .maintenance import MaintenanceJob
from .graph_review import GraphReviewService
from .models import (
    ConversationMode,
    ConversationRequest,
    ConversationResponse,
    HealthResponse,
)
from .router import MessageRouter

LOG_DIR = Path(os.environ.get("LOG_DIR", "./logs"))

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
knowledge_store = KnowledgeStore()
message_router = MessageRouter(conversation_db, knowledge_store)
maintenance_job = MaintenanceJob(knowledge_store)
graph_review = GraphReviewService(knowledge_store, conversation_db, maintenance_job)
WEB_DIR = Path(__file__).parent / "web"


@asynccontextmanager
async def lifespan(app: FastAPI):
    await conversation_db.connect()
    knowledge_store.connect()
    knowledge_store.sync_media_catalog()
    maintenance_job.start_scheduler()
    yield
    maintenance_job.stop()
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


@app.post("/maintenance/run")
async def run_maintenance() -> dict:
    """Manually trigger the nightly maintenance job."""
    result = await maintenance_job.run_now()
    return result


@app.get("/graph", include_in_schema=False)
async def graph_page() -> FileResponse:
    """Private visual explorer and parent-directed graph maintenance UI."""
    return FileResponse(WEB_DIR / "graph.html")


@app.get("/api/graph")
async def graph_snapshot() -> dict:
    return knowledge_store.get_graph_snapshot()


@app.post("/api/graph/review-sessions")
async def create_graph_review_session(body: dict | None = None) -> dict:
    title = (body or {}).get("title", "Graph review")
    return await conversation_db.create_graph_review_session(str(title)[:120])


@app.get("/api/graph/review-sessions")
async def list_graph_review_sessions() -> list[dict]:
    return await conversation_db.list_graph_review_sessions()


@app.get("/api/graph/review-sessions/{session_id}")
async def get_graph_review_session(session_id: str) -> dict:
    session = await conversation_db.get_graph_review_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Review session not found")
    return session


@app.post("/api/graph/review-sessions/{session_id}/messages")
async def send_graph_review_message(session_id: str, body: dict) -> dict:
    text = str(body.get("text", "")).strip()
    if not text:
        raise HTTPException(status_code=422, detail="text is required")
    try:
        return await graph_review.handle_message(session_id, text)
    except KeyError:
        raise HTTPException(status_code=404, detail="Review session not found")
