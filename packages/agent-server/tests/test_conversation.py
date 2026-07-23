"""Tests for the conversation API."""

from pathlib import Path

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


async def test_conversation_basic(client: AsyncClient):
    payload = {
        "text": "Hello!",
        "conversation_id": "test-1",
        "language": "en",
        "source": "assist",
    }
    resp = await client.post("/conversation", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert "reply_text" in data
    assert data["mode"] == "chat"
    assert isinstance(data["continue_conversation"], bool)


async def test_conversation_returns_response_on_llm_failure(client: AsyncClient):
    """When LLM fails, server still returns a valid response."""
    payload = {
        "text": "Tell me something fun!",
        "conversation_id": "test-fallback",
        "language": "en",
        "source": "assist",
    }
    resp = await client.post("/conversation", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["reply_text"]) > 0
    assert data["continue_conversation"] is True


async def test_conversation_returns_media_play_action(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
):
    async def fake_generate_json(system_prompt: str, user_text: str) -> dict:
        return {"media_id": "bedtime", "reason": "matched test catalog item"}

    monkeypatch.setattr(media, "generate_json", fake_generate_json)

    payload = {
        "text": "Please play bedtime music",
        "conversation_id": "test-media-play",
        "language": "en",
        "source": "assist",
    }
    resp = await client.post("/conversation", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert data["reply_text"] == "Okay, I'll play Bedtime."
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


async def test_conversation_uses_llm_selector_when_no_title_match(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
):
    async def fake_generate_json(system_prompt: str, user_text: str) -> dict:
        return {"media_id": "story", "reason": "matched via LLM"}

    monkeypatch.setattr(media, "generate_json", fake_generate_json)

    payload = {
        "text": "Can you play something to listen to?",
        "conversation_id": "test-media-llm",
        "language": "en",
        "source": "assist",
    }
    resp = await client.post("/conversation", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert data["reply_text"] == "Okay, I'll play Story."
    assert (
        data["actions"][0]["data"]["service_data"]["media_content_id"]
        == "media-source://media_source/local/kids_robot/story.mp3"
    )


async def test_conversation_uses_llm_for_title_match(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
):
    async def fake_generate_json(system_prompt: str, user_text: str) -> dict:
        return {"media_id": "bingo", "reason": "user asked for bingo"}

    monkeypatch.setattr(media, "generate_json", fake_generate_json)

    payload = {
        "text": "Please play bingo",
        "conversation_id": "test-media-bingo",
        "language": "en",
        "source": "assist",
    }
    resp = await client.post("/conversation", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert data["reply_text"] == "Okay, I'll play Bingo."
    assert (
        data["actions"][0]["data"]["service_data"]["media_content_id"]
        == "media-source://media_source/local/kids_robot/BINGO.mp4"
    )


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


async def test_status(client: AsyncClient):
    resp = await client.get("/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "running"
