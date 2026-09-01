"""Tests for the conversation API."""

from pathlib import Path
import pytest
from httpx import ASGITransport, AsyncClient

from agent_server import media
from agent_server.app import app, conversation_db, knowledge_store


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
    knowledge_store.connect()
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


async def test_conversation_sets_timer_from_llm(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
):
    from agent_server.modes import chat

    async def fake_generate_chat_json(system_prompt, history, user_text):
        return {
            "reply_text": "Okay! I'll let you know in 5 minutes.",
            "media_ids": [],
            "timer_seconds": 300,
        }

    monkeypatch.setattr(chat, "generate_chat_json", fake_generate_chat_json)

    resp = await client.post(
        "/conversation",
        json={"text": "Set a timer for five minutes", "conversation_id": "timer-1"},
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["reply_text"] == "Okay! I'll let you know in 5 minutes."
    assert data["actions"][0]["data"] == {
        "domain": "kids_robot",
        "service": "start_timer",
        "service_data": {"duration_seconds": 300},
    }


async def test_llm_can_return_a_timer_action(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
):
    from agent_server.modes import chat

    async def fake_generate_chat_json(system_prompt, history, user_text):
        return {
            "reply_text": "Okay! I'll let you know in 10 seconds.",
            "media_ids": [],
            "timer_seconds": 10,
        }

    monkeypatch.setattr(chat, "generate_chat_json", fake_generate_chat_json)
    resp = await client.post(
        "/conversation",
        json={"text": "Could you remind me in 10 seconds?", "conversation_id": "timer-llm"},
    )

    assert resp.status_code == 200
    action = resp.json()["actions"][0]["data"]
    assert action["service"] == "start_timer"
    assert action["service_data"] == {"duration_seconds": 10}

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


async def test_graph_review_page_and_snapshot(client: AsyncClient):
    page = await client.get("/graph")
    assert page.status_code == 200
    assert "Plata’s knowledge graph" in page.text

    graph = await client.get("/api/graph")
    assert graph.status_code == 200
    assert set(graph.json()) == {"nodes", "links"}


async def test_unchanged_media_sync_preserves_updated_timestamp(client: AsyncClient):
    media_id = knowledge_store.upsert_media(
        "timestamp-test-media", "Timestamp Test", "timestamp-test.mp3", media_content_type="music"
    )
    before = knowledge_store.store.get_entity(media_id)
    assert before

    same_media_id = knowledge_store.upsert_media(
        "timestamp-test-media", "Timestamp Test", "timestamp-test.mp3", media_content_type="music"
    )
    after = knowledge_store.store.get_entity(same_media_id)
    assert after
    assert after.updated_at == before.updated_at

    knowledge_store.store.delete_entity(media_id)


async def test_graph_review_persists_and_applies_requested_update(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
):
    from agent_server import graph_review

    message_id = knowledge_store.record_message(
        text="This is review test evidence.", conversation_id="graph-review-test", topics=[]
    )
    knowledge_store.record_facts(
        [{"subject": "Review Test", "relation": "is_a", "object": "sample", "confidence": 0.9}],
        message_id,
    )
    fact = knowledge_store.store.get_entity_by_identifier("fact_key", "review test|is_a|sample")
    assert fact

    async def fake_review_response(system_prompt, history, user_text):
        assert "Review Test is a sample" in system_prompt
        assert history[-1] == {"role": "user", "text": user_text}
        return {
            "reply_text": "I updated the display wording.",
            "actions": [{"type": "update", "id": fact.id[:8], "new_name": "Review Test is a test sample"}],
        }

    monkeypatch.setattr(graph_review, "generate_chat_json", fake_review_response)
    created = await client.post("/api/graph/review-sessions", json={"title": "Test review"})
    assert created.status_code == 200
    session_id = created.json()["id"]

    response = await client.post(
        f"/api/graph/review-sessions/{session_id}/messages",
        json={"text": "Please improve this fact's display wording."},
    )
    assert response.status_code == 200
    assert response.json()["actions"] == [{
        "action": {"type": "update", "id": fact.id[:8], "new_name": "Review Test is a test sample"},
        "applied": True,
    }]
    assert knowledge_store.store.get_entity(fact.id).name == "Review Test is a test sample"

    session = await client.get(f"/api/graph/review-sessions/{session_id}")
    assert [message["role"] for message in session.json()["messages"]] == ["user", "model"]
    assert session.json()["actions"][0]["applied"] is True
    assert session.json()["title"] == "Please improve this fact's display wording."
