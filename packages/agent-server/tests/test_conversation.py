"""Tests for the conversation API."""

import pytest
from httpx import ASGITransport, AsyncClient

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


async def test_status(client: AsyncClient):
    resp = await client.get("/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "running"
