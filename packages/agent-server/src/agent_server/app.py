"""FastAPI application for the kids robot conversation agent."""

import logging
import os
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from contextlib import asynccontextmanager

from .journal import JournalService, NewJournal, JournalPatch, EntryRequest
from .logbook_chat import LogbookChat, ChatRequest
from .agent_stream import browser_agent_router
from .graph_agent import GraphBrowserReview
from .household import GuidanceRequest, PersonRequest, MemorySettings, BehaviorReview
from .context import ConversationDB
from .knowledge import KnowledgeStore
from .maintenance import MaintenanceJob
from .graph_review import GraphReviewService, ReviewUnavailable
from .models import (
    ConversationMode,
    ConversationRequest,
    ConversationResponse,
    FamilyValueRequest,
    HealthResponse,
    PlaybackEvent,
)
from .router import MessageRouter
from .playlist_sync import PlaylistSync, sync_router, jobs_router, MAINTENANCE_ID

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
graph_browser = GraphBrowserReview(graph_review)
journals = JournalService(knowledge_store)
logbook_chat = LogbookChat(journals)
playlist_sync = PlaylistSync(maintenance=maintenance_job, on_catalog_changed=knowledge_store.sync_media_catalog)
WEB_DIR = Path(__file__).parent / "web"
UI_DIST = Path(os.environ.get("UI_DIST", str(Path(__file__).resolve().parents[3] / "web-ui" / "dist")))


@asynccontextmanager
async def lifespan(app: FastAPI):
    await conversation_db.connect()
    knowledge_store.connect()
    knowledge_store.sync_media_catalog()
    playlist_sync.start()
    try:
        yield
    finally:
        await playlist_sync.stop()
        await conversation_db.close()


app = FastAPI(title="Kids Robot Agent Server", version="0.1.0", lifespan=lifespan)

app.include_router(sync_router(playlist_sync))
app.include_router(jobs_router(playlist_sync))
app.include_router(browser_agent_router(logbook_chat))
app.include_router(browser_agent_router(graph_browser, "graph"))
if UI_DIST.is_dir():
    app.mount("/workspace", StaticFiles(directory=UI_DIST, html=True), name="workspace")


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


@app.post("/playback/sessions/{session_id}/events")
async def playback_event(session_id: str, event: PlaybackEvent) -> dict:
    """Record trusted playback progress reported by the HA integration."""
    try:
        state = knowledge_store.update_playback(
            session_id, event.event, event.track_index
        )
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return {"status": "ok", "playback": state}


@app.post("/maintenance/run")
async def run_maintenance() -> dict:
    """Manually trigger the nightly maintenance job."""
    result = await playlist_sync.sync(playlist_sync.get(MAINTENANCE_ID), trigger="manual")
    return result


def workspace_page(filename: str, active: str) -> HTMLResponse:
    navigation = (WEB_DIR / "navigation.html").read_text().replace(
        f'id="view-{active}"', f'id="view-{active}" aria-current="page"'
    )
    html = (WEB_DIR / filename).read_text().replace("<!-- workspace-navigation -->", navigation)
    return HTMLResponse(html, headers={"Cache-Control": "no-store"})


@app.get("/web/workspace.css", include_in_schema=False)
async def workspace_styles() -> FileResponse:
    return FileResponse(WEB_DIR / "workspace.css", media_type="text/css", headers={"Cache-Control": "no-store"})


@app.get("/jobs", include_in_schema=False)
async def jobs_page():
    if (UI_DIST / "index.html").is_file():
        return RedirectResponse("/workspace/#jobs", status_code=307)
    return workspace_page("jobs.html", "jobs")


@app.get("/graph", include_in_schema=False)
async def graph_page():
    """Private visual explorer and parent-directed graph maintenance UI."""
    if (UI_DIST / "index.html").is_file():
        return RedirectResponse("/workspace/", status_code=307)
    return workspace_page("graph.html", "journal")


@app.get("/", include_in_schema=False)
async def workspace_home():
    return RedirectResponse("/workspace/" if (UI_DIST / "index.html").is_file() else "/graph")


@app.get("/legacy/graph", include_in_schema=False)
async def legacy_graph_page() -> HTMLResponse:
    return workspace_page("graph.html", "journal")


@app.get("/legacy/jobs", include_in_schema=False)
async def legacy_jobs_page() -> HTMLResponse:
    return workspace_page("jobs.html", "jobs")


@app.get("/api/graph")
async def graph_snapshot() -> dict:
    return knowledge_store.get_graph_snapshot()


@app.get("/api/guidance-documents")
async def list_guidance_documents(include_history: bool = False) -> list[dict]:
    return knowledge_store.get_guidance_documents(include_history)


@app.post("/api/guidance-documents")
async def save_guidance_document(document: GuidanceRequest) -> dict:
    return knowledge_store.save_guidance_document(document)


@app.get("/api/people")
async def list_people() -> list[dict]:
    return knowledge_store.get_people()


@app.post("/api/people")
async def create_person(person: PersonRequest) -> dict:
    return knowledge_store.create_person(person)


@app.get("/api/learning-events")
async def list_learning_events(person_id: str | None = None, topic: str | None = None, limit: int = 50) -> list[dict]:
    return knowledge_store.get_events("learning_event", person_id, topic, limit)


@app.get("/api/behavior-events")
async def list_behavior_events(person_id: str | None = None, limit: int = 50) -> list[dict]:
    return knowledge_store.get_events("behavior_event", person_id, limit=limit)


@app.patch("/api/behavior-events/{event_id}")
async def review_behavior_event(event_id: str, review: BehaviorReview) -> dict:
    try:
        return knowledge_store.review_behavior_event(event_id, review)
    except KeyError as error:
        raise HTTPException(status_code=404, detail="Behavior event not found") from error


@app.get("/api/memory-settings")
async def get_memory_settings() -> MemorySettings:
    return knowledge_store.get_memory_settings()


@app.put("/api/memory-settings")
async def save_memory_settings(settings: MemorySettings) -> MemorySettings:
    return knowledge_store.save_memory_settings(settings)


@app.get("/api/family-values")
async def list_family_values() -> list[dict]:
    return knowledge_store.get_family_values()


@app.post("/api/family-values")
async def upsert_family_value(value: FamilyValueRequest) -> dict:
    try:
        return knowledge_store.upsert_family_value(
            name=value.name,
            description=value.description,
            guidance=value.guidance,
            key=value.key,
            enabled=value.enabled,
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.get("/api/kid-events")
async def list_kid_events(child_name: str | None = None, limit: int = 50) -> list[dict]:
    return knowledge_store.get_kid_events(child_name=child_name, limit=limit)


@app.post("/api/graph/review-sessions")
async def create_graph_review_session(body: dict | None = None) -> dict:
    title = (body or {}).get("title", "Graph review")
    return await conversation_db.create_graph_review_session(str(title)[:120])


@app.get("/api/graph/review-sessions")
async def list_graph_review_sessions() -> list[dict]:
    return await graph_browser.sessions()


@app.get("/api/graph/review-sessions/{session_id}")
async def get_graph_review_session(session_id: str) -> dict:
    try:
        return await graph_browser.session(session_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Review session not found")


@app.post("/api/graph/review-sessions/{session_id}/messages")
async def send_graph_review_message(session_id: str, body: dict) -> dict:
    text = str(body.get("text", "")).strip()
    if not text:
        raise HTTPException(status_code=422, detail="text is required")
    try:
        return await graph_review.handle_message(session_id, text)
    except KeyError:
        raise HTTPException(status_code=404, detail="Review session not found")
    except ReviewUnavailable:
        raise HTTPException(status_code=503, detail="The review assistant is temporarily unavailable. No changes were applied. Please try again.")


@app.get("/api/logbook/sessions")
async def logbook_sessions():
    return logbook_chat.sessions()


@app.post("/api/logbook/sessions")
async def new_logbook_session():
    return logbook_chat.create_session()


@app.get("/api/logbook/sessions/{session_id}")
async def get_logbook_session(session_id: str):
    try:
        return logbook_chat.session(session_id)
    except KeyError:
        raise HTTPException(404, "Conversation not found")


@app.post("/api/logbook/sessions/{session_id}/messages")
async def logbook_message(session_id: str, request: ChatRequest):
    try:
        return await logbook_chat.send(session_id, request)
    except KeyError:
        raise HTTPException(404, "Conversation or note not found")
    except Exception:
        logger.warning("Log book chat failed; saved messages retained", exc_info=False)
        raise HTTPException(503, "Could not finish. Your saved message is available below; retry to continue.")


@app.get("/api/journals")
async def list_journals():
    return journals.list()


@app.post("/api/journals")
async def new_journal(request: NewJournal):
    return journals.create(request)


@app.get("/api/journals/{journal_id}")
async def get_journal(journal_id: str):
    try:
        return journals.get(journal_id)
    except KeyError:
        raise HTTPException(404, "Reflection not found")


@app.patch("/api/journals/{journal_id}")
async def patch_journal(journal_id: str, request: JournalPatch):
    try:
        return journals.patch(journal_id, request)
    except KeyError:
        raise HTTPException(404, "Reflection not found")


@app.post("/api/journals/{journal_id}/entries")
async def append_journal(journal_id: str, request: EntryRequest):
    try:
        return journals.append(journal_id, request)
    except KeyError:
        raise HTTPException(404, "Reflection not found")
    except ValueError as error:
        raise HTTPException(422, str(error))


@app.post("/api/journals/{journal_id}/organize")
async def organize_journal(journal_id: str):
    try:
        return await journals.organize(journal_id)
    except KeyError:
        raise HTTPException(404, "Reflection not found")
    except Exception:
        logger.warning("Reflection organization failed; original entries retained", exc_info=False)
        raise HTTPException(503, "Your original entry is saved. Organization could not finish; please retry.")


@app.get("/web/journal.js", include_in_schema=False)
async def journal_script():
    return FileResponse(WEB_DIR / "journal.js", media_type="application/javascript", headers={"Cache-Control": "no-store"})
