"""Tests for the conversation API."""

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from agent_server import media
from agent_server.app import app, conversation_db


@pytest.fixture
def media_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Create a temp media directory with test files."""
    (tmp_path / "bedtime.mp3").write_bytes(b"fake")
    (tmp_path / "story.mp3").write_bytes(b"fake")
    (tmp_path / "BINGO.mp4").write_bytes(b"fake")
    monkeypatch.setattr(media, "MEDIA_DIR", tmp_path)
    return tmp_path


@pytest.fixture
async def client(media_dir):
    await conversation_db.connect()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    await conversation_db.close()


async def test_health(client: AsyncClient):
    resp = await client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"


async def test_conversation_returns_chat_response(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
):
    from agent_server.modes import chat

    async def fake_generate_chat_json(system_prompt, history, user_text):
        return {"reply_text": "Hello friend!", "media_id": None}

    monkeypatch.setattr(chat, "generate_chat_json", fake_generate_chat_json)

    payload = {
        "text": "Hello!",
        "conversation_id": "test-1",
        "language": "en",
        "source": "assist",
    }
    resp = await client.post("/conversation", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert data["reply_text"] == "Hello friend!"
    assert data["mode"] == "chat"
    assert data["actions"] == []
    assert data["continue_conversation"] is True


async def test_conversation_returns_media_play_action(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
):
    from agent_server.modes import chat

    async def fake_generate_chat_json(system_prompt, history, user_text):
        return {"reply_text": "Let's listen to Bedtime!", "media_id": "bedtime"}

    monkeypatch.setattr(chat, "generate_chat_json", fake_generate_chat_json)

    payload = {
        "text": "Play some bedtime music",
        "conversation_id": "test-media-play",
        "language": "en",
        "source": "assist",
    }
    resp = await client.post("/conversation", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert data["reply_text"] == "Let's listen to Bedtime!"
    assert data["continue_conversation"] is False
    assert data["actions"] == [
        {
            "type": "ha_service",
            "target": None,
            "data": {
                "domain": "media_player",
                "service": "play_media",
                "service_data": {
                    "media_content_id": "media-source://media_source/local/kids_robot/bedtime.mp3",
                    "media_content_type": "music",
                },
            },
        }
    ]


async def test_conversation_returns_media_stop_action(client: AsyncClient):
    payload = {
        "text": "Stop the music",
        "conversation_id": "test-media-stop",
        "language": "en",
        "source": "assist",
    }
    resp = await client.post("/conversation", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert data["reply_text"] == "Okay, I'll stop the audio."
    assert data["actions"][0]["data"]["service"] == "media_stop"


async def test_conversation_fallback_on_llm_failure(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
):
    from agent_server.modes import chat

    async def fake_generate_chat_json(system_prompt, history, user_text):
        raise RuntimeError("LLM down")

    monkeypatch.setattr(chat, "generate_chat_json", fake_generate_chat_json)

    payload = {
        "text": "Tell me something fun!",
        "conversation_id": "test-fallback",
        "language": "en",
        "source": "assist",
    }
    resp = await client.post("/conversation", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert "fuzzy" in data["reply_text"]
    assert data["continue_conversation"] is True


async def test_conversation_unknown_media_id_falls_back_to_chat(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
):
    from agent_server.modes import chat

    async def fake_generate_chat_json(system_prompt, history, user_text):
        return {"reply_text": "Let me play that!", "media_id": "nonexistent_song"}

    monkeypatch.setattr(chat, "generate_chat_json", fake_generate_chat_json)

    payload = {
        "text": "Play something random",
        "conversation_id": "test-unknown-media",
        "language": "en",
        "source": "assist",
    }
    resp = await client.post("/conversation", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert data["reply_text"] == "Let me play that!"
    assert data["actions"] == []
    assert data["continue_conversation"] is True


async def test_status(client: AsyncClient):
    resp = await client.get("/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "running"
