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

    async def fake_generate_chat_json(system_prompt, history, user_text, **kwargs):
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

    async def fake_generate_chat_json(system_prompt, history, user_text, **kwargs):
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


async def test_conversation_returns_media_stop_action(client: AsyncClient, monkeypatch):
    from agent_server.modes import chat

    async def generate(system_prompt, history, user_text, **kwargs):
        assert 'Interpret negations' in system_prompt
        return {'reply_text': "Okay, I'll stop the audio.", 'media_operation': 'stop', 'media_ids': []}

    monkeypatch.setattr(chat, 'generate_chat_json', generate)
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
    assert data["actions"][0]["data"]["service"] == "stop_playback"


async def test_conversation_sets_timer_from_llm(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
):
    from agent_server.modes import chat

    async def fake_generate_chat_json(system_prompt, history, user_text, **kwargs):
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

    async def fake_generate_chat_json(system_prompt, history, user_text, **kwargs):
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

    async def fake_generate_chat_json(system_prompt, history, user_text, **kwargs):
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

    async def fake_generate_chat_json(system_prompt, history, user_text, **kwargs):
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


async def test_cc_transcription_variant_is_sent_to_llm_with_guidance(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
):
    from agent_server.modes import chat

    called = False

    async def fake_generate_chat_json(system_prompt, history, user_text, **kwargs):
        nonlocal called
        called = True
        assert user_text == "Play CC Psycho 3 week one"
        assert '"CC Psycho 3"' in system_prompt
        return {"reply_text": "I understand the CC request.", "media_ids": []}

    monkeypatch.setattr(chat, "generate_chat_json", fake_generate_chat_json)
    response = await client.post(
        "/conversation",
        json={
            "text": "Play CC Psycho 3 week one",
            "conversation_id": "cc-llm-managed",
        },
    )

    assert response.status_code == 200
    assert called is True
    assert response.json()["reply_text"] == "I understand the CC request."


def test_cycle3_playlists_are_compacted_in_llm_prompt():
    from agent_server.modes.chat import _format_playlist_catalog

    playlists = {f"cc_cycle3_week_{week}": [] for week in range(1, 25)}
    playlists["bedtime_favorites"] = []

    formatted = _format_playlist_catalog(playlists)

    assert "cc_cycle3_week_${weekN}" in formatted
    assert "where ${weekN} is 1 through 24" in formatted
    assert "- bedtime_favorites" in formatted
    assert "- cc_cycle3_week_1\n" not in formatted


def test_incomplete_cycle3_playlist_set_is_listed_explicitly():
    from agent_server.modes.chat import _format_playlist_catalog

    formatted = _format_playlist_catalog({"cc_cycle3_week_1": []})

    assert formatted == "- cc_cycle3_week_1"


async def test_status(client: AsyncClient):
    resp = await client.get("/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "running"


async def test_graph_review_page_and_snapshot(client: AsyncClient):
    page = await client.get("/legacy/graph")
    assert page.status_code == 200
    assert "Plata’s knowledge graph" in page.text

    graph = await client.get("/api/graph")
    assert graph.status_code == 200
    assert set(graph.json()) == {"nodes", "links"}


async def test_family_values_api_upserts_and_lists_values(client: AsyncClient):
    created = await client.post(
        "/api/family-values",
        json={
            "name": "Helpfulness",
            "description": "Look for chances to serve others.",
            "guidance": "Encourage specific helping behavior.",
        },
    )

    assert created.status_code == 200
    value = created.json()
    assert value["key"] == "helpfulness"
    assert value["name"] == "Helpfulness"
    assert value["enabled"] is True

    listed = await client.get("/api/family-values")
    assert listed.status_code == 200
    assert any(item["key"] == "helpfulness" for item in listed.json())


async def test_family_values_are_included_in_chat_prompt(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
):
    from agent_server.modes import chat

    knowledge_store.upsert_family_value(
        name="Helpfulness",
        description="Look for chances to serve others.",
        guidance="Encourage specific helping behavior.",
    )

    async def fake_generate_chat_json(system_prompt, history, user_text, **kwargs):
        assert "Family values for encouragement and correction" in system_prompt
        assert "helpfulness: Helpfulness" in system_prompt
        return {
            "reply_text": "That was a kind way to help.",
            "media_ids": [],
            "kid_events": [],
        }

    monkeypatch.setattr(chat, "generate_chat_json", fake_generate_chat_json)

    response = await client.post(
        "/conversation",
        json={
            "text": "Sample Child helped clean up toys.",
            "conversation_id": "family-value-prompt",
        },
    )

    assert response.status_code == 200
    assert response.json()["reply_text"] == "That was a kind way to help."


async def test_parent_described_kid_event_is_logged_with_value(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
):
    from agent_server.modes import chat

    value = knowledge_store.upsert_family_value(
        name="Helpfulness",
        description="Look for chances to serve others.",
        guidance="Encourage specific helping behavior.",
    )

    async def fake_generate_chat_json(system_prompt, history, user_text, **kwargs):
        return {
            "reply_text": "Sample Child showed helpfulness by cleaning up.",
            "media_ids": [],
            "topics": ["helpfulness"],
            "facts": [],
            "kid_events": [
                {
                    "child_name": "Sample Child",
                    "summary": "helped clean up toys",
                    "event_type": "encouragement",
                    "matched_value_keys": ["helpfulness"],
                    "parent_note": "reward candidate",
                    "confidence": 0.9,
                }
            ],
        }

    monkeypatch.setattr(chat, "generate_chat_json", fake_generate_chat_json)

    response = await client.post(
        "/conversation",
        json={
            "text": "Sample Child helped clean up toys.",
            "conversation_id": "kid-event-log",
        },
    )

    assert response.status_code == 200
    assert response.json()["reply_text"] == "Sample Child showed helpfulness by cleaning up."

    events = (await client.get("/api/kid-events?child_name=Sample%20Child")).json()
    assert events[0]["summary"] == "helped clean up toys"
    assert events[0]["event_type"] == "encouragement"
    assert events[0]["matched_value_keys"] == ["helpfulness"]

    graph = await client.get("/api/graph")
    links = graph.json()["links"]
    assert any(link["type"] == "reflects" and link["to"] == value["id"] for link in links)


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

    async def fake_review_response(system_prompt, history, user_text, **kwargs):
        assert "Review Test is a sample" in system_prompt
        assert history == []  # Current user text is supplied separately, exactly once.
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


async def test_cc_week_and_continue_route_whole_playlists_with_saved_progress(client, media_dir, monkeypatch):
    from agent_server.modes import chat
    for week in (3, 4):
        folder = media_dir / 'cc_cycle3' / f'week_{week}'
        folder.mkdir(parents=True)
        for subject in ('English', 'Science'):
            (folder / f'{subject}.mp3').write_bytes(b'audio')
    media.invalidate_playlist_cache()
    knowledge_store.sync_media_catalog()
    selected = {'reply_text': 'Playing.', 'media_ids': ['cc_cycle3_week_3'], 'media_operation': 'play'}

    async def generate(system_prompt, history, user_text, **kwargs):
        assert 'cc_week_3' in system_prompt
        return selected

    monkeypatch.setattr(chat, 'generate_chat_json', generate)
    async def request(text):
        response = await client.post('/conversation', json={'text': text, 'conversation_id': 'sample-cc-playback'})
        assert response.status_code == 200
        return response.json()

    first = await request('Play CC week 3 songs')
    data = first['actions'][0]['data']['service_data']
    assert len(data['tracks']) == 2
    assert all('week_3/' in track for track in data['tracks'])
    knowledge_store.update_playback(data['session_id'], 'track_completed', 0)
    knowledge_store.update_playback(data['session_id'], 'stopped')
    selected.update(media_ids=['cc_cycle3_week_3'], media_operation='resume')
    resumed = await request('Continue playing CC songs')
    resumed_data = resumed['actions'][0]['data']['service_data']
    assert resumed_data['start_index'] == 1
    knowledge_store.update_playback(data['session_id'], 'track_completed', 1)
    selected.update(media_ids=['cc_cycle3_week_4'], media_operation='play', reply_text='Ready for week 4!')
    advanced = await request('Continue playing CC songs')
    next_data = advanced['actions'][0]['data']['service_data']
    assert all('week_4/' in track for track in next_data['tracks'])
    assert next_data['start_index'] == 0
    assert advanced['reply_text'] == 'Ready for week 4!'
    media.invalidate_playlist_cache()


async def test_playback_intent_and_reply_are_owned_by_model(client, media_dir, monkeypatch):
    from agent_server.modes import chat
    for week in (3, 4):
        folder = media_dir / 'cc_cycle3' / f'week_{week}'
        folder.mkdir(parents=True)
        (folder / 'Science.mp3').write_bytes(b'audio')
    media.invalidate_playlist_cache()
    knowledge_store.sync_media_catalog()
    previous = knowledge_store.begin_playback('cc_cycle3_week_3', 1)
    knowledge_store.update_playback(previous['session_id'], 'track_completed', 0)
    selected = {'reply_text': 'Let’s hear that week again!', 'media_ids': ['cc_cycle3_week_3'],
                'media_operation': 'restart', 'reset_playlist_ids': []}

    async def generate(system_prompt, history, user_text, **kwargs):
        assert 'cc_cycle3_week_3: 1 of 1 completed; remaining: 0' in system_prompt
        assert 'it does not infer a cycle or choose a next week' in system_prompt
        assert 'cc_cycle3_week_4' in system_prompt
        return selected

    monkeypatch.setattr(chat, 'generate_chat_json', generate)
    response = await client.post('/conversation', json={'text': 'Continue CC, but repeat that week first', 'conversation_id': 'sample-repeat'})
    data = response.json()
    assert data['reply_text'] == selected['reply_text']
    assert all('week_3/' in file for file in data['actions'][0]['data']['service_data']['tracks'])
    assert data['actions'][0]['data']['service_data']['start_index'] == 0
    media.invalidate_playlist_cache()


async def test_stop_keywords_do_not_override_llm_interpretation(client, monkeypatch):
    from agent_server.modes import chat

    async def generate(system_prompt, history, user_text, **kwargs):
        assert user_text == "Don't stop the music; I am only asking a question."
        return {'reply_text': 'Sure, what would you like to know?', 'media_ids': []}

    monkeypatch.setattr(chat, 'generate_chat_json', generate)
    response = await client.post('/conversation', json={'text': "Don't stop the music; I am only asking a question.", 'conversation_id': 'sample-negation'})
    assert response.json()['actions'] == []
    assert response.json()['reply_text'] == 'Sure, what would you like to know?'


async def test_completed_resume_does_not_substitute_next_week(client, media_dir, monkeypatch):
    from agent_server.modes import chat
    for week in (3, 4):
        folder = media_dir / 'cc_cycle3' / f'week_{week}'
        folder.mkdir(parents=True)
        (folder / 'Science.mp3').write_bytes(b'audio')
    media.invalidate_playlist_cache()
    knowledge_store.sync_media_catalog()
    first = knowledge_store.begin_playback('cc_cycle3_week_3', 1)
    knowledge_store.update_playback(first['session_id'], 'track_completed', 0)

    async def generate(system_prompt, history, user_text, **kwargs):
        return {'reply_text': 'That week is complete.', 'media_ids': ['cc_cycle3_week_3'], 'media_operation': 'resume'}

    monkeypatch.setattr(chat, 'generate_chat_json', generate)
    response = await client.post('/conversation', json={'text': 'Continue CC songs', 'conversation_id': 'sample-completed'})
    assert response.json()['actions'] == []
    assert response.json()['reply_text'] == 'That week is complete.'
    assert knowledge_store.store.get_entity(first['session_id']).properties['status'] == 'completed'
    media.invalidate_playlist_cache()


@pytest.mark.parametrize('operation,reset_ids,expected', [
    ('restart', ['cc_cycle3_week_4'], 'superseded'),
    ('resume', ['cc_cycle3_week_4'], 'completed'),
    ('restart', ['cc_cycle3_week_4', 'unknown'], 'completed'),
])
async def test_model_reset_scope_is_validated(client, media_dir, monkeypatch, operation, reset_ids, expected):
    from agent_server.modes import chat
    for week in (3, 4):
        folder = media_dir / 'cc_cycle3' / f'week_{week}'
        folder.mkdir(parents=True)
        (folder / 'Science.mp3').write_bytes(b'audio')
    media.invalidate_playlist_cache()
    knowledge_store.sync_media_catalog()
    previous = knowledge_store.begin_playback('cc_cycle3_week_4', 1)
    knowledge_store.update_playback(previous['session_id'], 'track_completed', 0)

    async def generate(system_prompt, history, user_text, **kwargs):
        return {'reply_text': 'Here we go.', 'media_ids': ['cc_cycle3_week_3'],
                'media_operation': operation, 'reset_playlist_ids': reset_ids}

    monkeypatch.setattr(chat, 'generate_chat_json', generate)
    response = await client.post('/conversation', json={'text': 'Start the sequence over', 'conversation_id': 'sample-reset'})
    assert response.status_code == 200
    assert knowledge_store.store.get_entity(previous['session_id']).properties['status'] == expected
    media.invalidate_playlist_cache()


@pytest.mark.parametrize('choice', [
    {'media_operation': 'stop'},
    {'media_ids': ['bedtime'], 'media_operation': 'restart', 'reset_playlist_ids': ['sample']},
    {'timer_seconds': 30},
])
async def test_telegram_capabilities_block_actions_before_state_changes(client, monkeypatch, choice):
    from agent_server.modes import chat
    from unittest.mock import Mock

    async def generate(system_prompt, history, user_text, **kwargs):
        assert 'Telegram text conversation' in system_prompt
        return {'reply_text': 'I did it.', **choice}

    monkeypatch.setattr(chat, 'generate_chat_json', generate)
    stop = Mock(side_effect=AssertionError('Must not mutate playback'))
    begin = Mock(side_effect=AssertionError('Must not begin playback'))
    reset = Mock(side_effect=AssertionError('Must not reset playback'))
    monkeypatch.setattr(knowledge_store, 'stop_active_playback', stop)
    monkeypatch.setattr(knowledge_store, 'begin_playback', begin)
    monkeypatch.setattr(knowledge_store, 'reset_playback', reset)
    response = await client.post('/conversation', json={
        'text': 'Sample device request', 'conversation_id': 'telegram:test', 'source': 'telegram'})
    assert response.status_code == 200
    assert response.json()['actions'] == []
    assert "aren't available" in response.json()['reply_text']
    stop.assert_not_called()
    begin.assert_not_called()
    reset.assert_not_called()
