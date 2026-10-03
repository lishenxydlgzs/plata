"""Exercise the real Strands Gemini provider with synthetic Google SDK chunks."""
import asyncio
from types import SimpleNamespace

import pytest
from google.genai import types, errors
from agent_server import agent_runtime


def chunk(text):
    return types.GenerateContentResponse(candidates=[types.Candidate(
        content=types.Content(role='model', parts=[types.Part(text=text)]), finish_reason='STOP')],
        usage_metadata=types.GenerateContentResponseUsageMetadata(prompt_token_count=2, candidates_token_count=3, total_token_count=5))


def fake_client(monkeypatch, generator):
    calls = []
    class Models:
        async def generate_content_stream(self, **kwargs):
            calls.append(kwargs)
            return generator(kwargs)
    monkeypatch.setattr(agent_runtime, 'get_client', lambda: SimpleNamespace(aio=SimpleNamespace(models=Models())))
    monkeypatch.setenv('GEMINI_MODELS', 'primary,fallback')
    return calls


async def test_structured_strands_turn_keeps_history_and_config(monkeypatch):
    async def generate(request):
        yield chunk('{"reply_text":')
        yield chunk('"Hello", "learning_events": []}')
    calls = fake_client(monkeypatch, generate)
    result = await agent_runtime.generate_chat_json('system', [{'role': 'user', 'text': 'Earlier sample question'},
        {'role': 'model', 'text': 'Earlier sample reply'}], 'New sample question', max_output_tokens=1536)
    assert result['reply_text'] == 'Hello'
    assert len(calls) == 1
    config = calls[0]['config']
    assert config['response_mime_type'] == 'application/json'
    assert config['max_output_tokens'] == 1536
    contents = calls[0]['contents']
    assert [c['role'] for c in contents] == ['user', 'model', 'user']
    assert contents[0]['parts'][0]['text'] == 'Earlier sample question'


async def test_buffered_voice_retries_partial_model_without_leaking_output(monkeypatch):
    async def generate(request):
        if request['model'] == 'primary':
            yield chunk('{"reply_text":"incomplete')
            raise errors.ServerError(503, {'error': {'message': 'temporarily unavailable'}})
        yield chunk('{"reply_text":"Recovered"}')
    calls = fake_client(monkeypatch, generate)
    assert (await agent_runtime.generate_chat_json('system', [], 'Hello'))['reply_text'] == 'Recovered'
    assert [c['model'] for c in calls] == ['primary', 'fallback']


async def test_browser_retries_throttle_before_content(monkeypatch):
    async def generate(request):
        if request['model'] == 'primary':
            raise errors.ClientError(429, {'error': {'status': 'RESOURCE_EXHAUSTED', 'message': 'quota'}})
        yield chunk('Recovered')
    calls = fake_client(monkeypatch, generate)
    model = agent_runtime.FallbackGeminiModel()
    events = [e async for e in model.stream([{'role': 'user', 'content': [{'text': 'Hello'}]}])]
    assert [c['model'] for c in calls] == ['primary', 'fallback']
    assert sum('messageStart' in e for e in events) == 1


async def test_visible_browser_output_is_never_replayed(monkeypatch):
    async def generate(request):
        yield chunk('A partial answer')
        raise errors.ServerError(503, {'error': {'message': 'unavailable'}})
    calls = fake_client(monkeypatch, generate)
    model = agent_runtime.FallbackGeminiModel()
    with pytest.raises(errors.ServerError):
        _ = [e async for e in model.stream([{'role': 'user', 'content': [{'text': 'Hello'}]}])]
    assert len(calls) == 1


async def test_strands_timeout_uses_next_configured_model(monkeypatch):
    async def generate(request):
        if request['model'] == 'primary':
            await asyncio.sleep(1)
        yield chunk('{"reply_text":"Hello"}')
    calls = fake_client(monkeypatch, generate)
    result = await agent_runtime.generate_chat_json('system', [], 'Hello', timeout_seconds=0.02)
    assert result['reply_text'] == 'Hello'
    assert [c['model'] for c in calls] == ['primary', 'fallback']


@pytest.mark.parametrize('reply,expected_service', [
    ({'reply_text': 'Hello friend!', 'media_ids': [], 'topics': []}, None),
    ({'reply_text': 'A short timer.', 'timer_seconds': 30, 'media_ids': []}, 'start_timer'),
])
async def test_home_assistant_contract_through_real_strands(monkeypatch, reply, expected_service):
    import json
    from httpx import AsyncClient, ASGITransport
    from agent_server.app import app, knowledge_store, conversation_db
    async def generate(request):
        yield chunk(json.dumps(reply))
    calls = fake_client(monkeypatch, generate)
    knowledge_store.connect(); await conversation_db.connect()
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
            response = await client.post('/conversation', json={'text': 'Sample request', 'conversation_id': 'sample-voice', 'language': 'en', 'source': 'assist'})
        assert response.status_code == 200
        value = response.json()
        assert set(('reply_text', 'mode', 'continue_conversation', 'actions')) <= value.keys()
        assert value['mode'] == 'chat'
        if expected_service:
            assert any(a['data']['service'] == expected_service for a in value['actions'])
        else:
            assert value['reply_text'] == reply['reply_text']
            assert value['actions'] == []
        assert len(calls) == 1
        history = await conversation_db.get_history('sample-voice')
        assert len(history) == 2
    finally:
        knowledge_store.store._db.close(); await conversation_db.close()
