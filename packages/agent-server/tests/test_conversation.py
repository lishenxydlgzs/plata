"""Tests for the conversation API."""

import pytest
from httpx import ASGITransport, AsyncClient

from agent_server import media
from agent_server.app import app, conversation_db


@pytest.fixture
async def client():
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
        return {"media_id": "bedtime_music", "reason": "matched test catalog item"}

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
    assert data["reply_text"] == "Okay, I'll play Bedtime Music."
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


async def test_conversation_uses_specific_media_catalog_match(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
):
    async def fake_generate_json(system_prompt: str, user_text: str) -> dict:
        return {"media_id": "bedtime_story", "reason": "matched exact phrase"}

    monkeypatch.setattr(media, "generate_json", fake_generate_json)

    payload = {
        "text": "Please play a bedtime story",
        "conversation_id": "test-media-specific",
        "language": "en",
        "source": "assist",
    }
    resp = await client.post("/conversation", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert data["reply_text"] == "Okay, I'll play Bedtime Story."
    assert (
        data["actions"][0]["data"]["service_data"]["media_content_id"]
        == "media-source://media_source/local/kids_robot/story.mp3"
    )


async def test_conversation_uses_catalog_alias_without_llm(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
):
    async def fake_generate_json(system_prompt: str, user_text: str) -> dict:
        raise AssertionError("direct catalog aliases should not call the LLM")

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
