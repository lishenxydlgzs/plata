"""Tests for the conversation API."""

import pytest
from httpx import ASGITransport, AsyncClient

from agent_server.app import app


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


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


async def test_mode_switch_to_teacher(client: AsyncClient):
    payload = {
        "text": "Let's do a quiz!",
        "conversation_id": "test-2",
        "language": "en",
        "source": "assist",
    }
    resp = await client.post("/conversation", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert data["mode"] == "teacher"


async def test_mode_switch_to_play(client: AsyncClient):
    payload = {
        "text": "Let's play pretend!",
        "conversation_id": "test-3",
        "language": "en",
        "source": "assist",
    }
    resp = await client.post("/conversation", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert data["mode"] == "play"


async def test_explicit_mode_start(client: AsyncClient):
    payload = {
        "text": "Start teaching",
        "conversation_id": "test-4",
        "language": "en",
        "source": "assist",
    }
    resp = await client.post("/mode/teacher/start", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert data["mode"] == "teacher"


async def test_invalid_mode(client: AsyncClient):
    payload = {
        "text": "hello",
        "conversation_id": "test-5",
        "language": "en",
        "source": "assist",
    }
    resp = await client.post("/mode/invalid/start", json=payload)
    assert resp.status_code == 400


async def test_status(client: AsyncClient):
    resp = await client.get("/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "running"
