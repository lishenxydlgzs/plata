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


def diagnostics(caplog, prefix):
    import json
    return [json.loads(r.getMessage().split(': ', 1)[1]) for r in caplog.records
            if r.name == agent_runtime.__name__ and r.getMessage().startswith(prefix + ': ')]


async def test_truncated_response_diagnostics_exclude_private_text(monkeypatch, caplog):
    caplog.set_level('INFO', logger=agent_runtime.__name__)
    async def generate(request):
        response = chunk('{"reply_text":"PRIVATE_RESPONSE_MARKER')
        response.candidates[0].finish_reason = 'MAX_TOKENS'
        yield response
    fake_client(monkeypatch, generate)
    from strands.types.exceptions import MaxTokensReachedException
    with pytest.raises(MaxTokensReachedException):
        await agent_runtime.generate_chat_json('PRIVATE_PROMPT_MARKER', [], 'PRIVATE_USER_MARKER', max_output_tokens=23)
    attempt, = diagnostics(caplog, 'Model attempt')
    assert attempt['stop_reason'] == 'max_tokens'
    assert attempt['max_output_tokens'] == 23 and attempt['json_mode'] is True
    assert attempt['input_tokens'] == 2 and attempt['output_tokens'] == 3
    assert attempt['total_tokens'] == 5 and attempt['duration_ms'] >= 0
    assert 'error_type=MaxTokensReachedException' in caplog.text
    assert not diagnostics(caplog, 'Structured response rejected')
    assert 'PRIVATE_' not in caplog.text


@pytest.mark.parametrize('recovered', [True, False])
async def test_recovery_diagnostics_share_id_and_report_outcome(monkeypatch, caplog, recovered):
    caplog.set_level('INFO', logger=agent_runtime.__name__)
    async def generate(request):
        yield chunk('PRIVATE_PROSE_MARKER' if len(calls) == 1 or not recovered else '{"reply_text":"PRIVATE_REPLY_MARKER"}')
    calls = fake_client(monkeypatch, generate)
    # A real tool-enabled turn, with no tool call needed for this synthetic reply.
    from strands import tool
    @tool
    def sample_lookup() -> str:
        """Return a synthetic lookup result."""
        return 'PRIVATE_TOOL_MARKER'
    if recovered:
        await agent_runtime.generate_chat_json('Sample system', [], 'Sample request', tools=[sample_lookup])
    else:
        with pytest.raises(ValueError):
            await agent_runtime.generate_chat_json('Sample system', [], 'Sample request', tools=[sample_lookup])
    attempts = diagnostics(caplog, 'Model attempt')
    rejections = diagnostics(caplog, 'Structured response rejected')
    accepted = diagnostics(caplog, 'Structured response accepted')
    assert [a['phase'] for a in attempts] == ['initial', 'recovery']
    assert [a['json_mode'] for a in attempts] == [False, True]
    assert len({d['diagnostic_id'] for d in [*attempts, *rejections, *accepted]}) == 1
    assert rejections[0]['response_shape'] == 'other_text' and rejections[0]['error_position'] == 0
    assert len(rejections) == (1 if recovered else 2)
    assert len(accepted) == (1 if recovered else 0)
    if accepted:
        assert accepted[0]['phase'] == 'recovery'
    assert 'PRIVATE_' not in caplog.text


async def test_fallback_attempt_diagnostics_do_not_log_exception_message(monkeypatch, caplog):
    caplog.set_level('INFO', logger=agent_runtime.__name__)
    async def generate(request):
        if request['model'] == 'primary':
            raise errors.ServerError(503, {'error': {'message': 'PRIVATE_ERROR_MARKER'}})
        yield chunk('{"reply_text":"Okay"}')
    fake_client(monkeypatch, generate)
    await agent_runtime.generate_chat_json('Sample system', [], 'Sample request')
    failed, completed = diagnostics(caplog, 'Model attempt')
    assert failed['outcome'] == 'failed' and failed['error_type'] == 'ServerError'
    assert failed['total_tokens'] is None and failed['stop_reason'] is None
    assert completed['outcome'] == 'completed' and completed['attempt'] == 2
    assert failed['diagnostic_id'] == completed['diagnostic_id']
    assert 'PRIVATE_ERROR_MARKER' not in caplog.text


async def test_format_recovery_does_not_repeat_durable_tool_effect(monkeypatch):
    from strands import tool
    effects = []
    @tool
    def sample_delivery() -> str:
        """Queue a synthetic delivery in an in-memory test list."""
        effects.append('queued')
        return 'Synthetic delivery queued successfully.'

    async def generate(request):
        if len(calls) == 1:
            yield types.GenerateContentResponse(candidates=[types.Candidate(
                content=types.Content(role='model', parts=[types.Part(function_call=types.FunctionCall(
                    name='sample_delivery', args={}))]), finish_reason='STOP')])
        elif len(calls) == 2:
            assert effects == ['queued']
            yield chunk('The synthetic delivery was queued.')
        else:
            import json
            assert not request['config'].get('tools')
            assert 'Synthetic delivery queued successfully.' in json.dumps(request['contents'])
            yield chunk('{"reply_text":"The synthetic delivery was queued."}')
    calls = fake_client(monkeypatch, generate)
    result = await agent_runtime.generate_chat_json('Respond in JSON.', [], 'Sample request', tools=[sample_delivery])
    assert result['reply_text'] == 'The synthetic delivery was queued.'
    assert effects == ['queued'] and len(calls) == 3


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


@pytest.mark.parametrize('reply,error', [('[]', 'expected object, got list'), ('{"tracks":', 'Expecting value')])
@pytest.mark.parametrize('diagnostics_enabled', [False, True])
async def test_rejected_response_diagnostics(monkeypatch, caplog, reply, error, diagnostics_enabled):
    async def generate(request):
        yield chunk(reply)
    fake_client(monkeypatch, generate)
    with pytest.raises(ValueError):
        await agent_runtime.generate_chat_json('private system', [], 'private user',
                                               log_response_on_error=diagnostics_enabled)
    assert error in caplog.text
    rejected, = diagnostics(caplog, 'Structured response rejected')
    assert rejected['model'] == 'primary'
    assert ('Rejected response excerpt' in caplog.text) is diagnostics_enabled
    assert 'private system' not in caplog.text
    assert 'private user' not in caplog.text


async def test_response_schema_reaches_provider_and_survives_fallback(monkeypatch):
    from agent_server.cc_catalog import classification_response_schema
    schema = classification_response_schema()

    async def generate(request):
        assert request['config']['response_json_schema'] == schema
        assert request['config']['response_mime_type'] == 'application/json'
        if request['model'] == 'primary':
            raise errors.ServerError(503, {'error': {'message': 'temporarily unavailable'}})
        yield chunk('{"tracks": []}')

    calls = fake_client(monkeypatch, generate)
    assert await agent_runtime.generate_chat_json('system', [], 'Sample', response_schema=schema) == {'tracks': []}
    assert [c['model'] for c in calls] == ['primary', 'fallback']


async def test_schema_with_tools_rejected_before_call(monkeypatch):
    def unexpected(*args, **kwargs):
        pytest.fail('No model call should be made')
    monkeypatch.setattr(agent_runtime, 'get_client', unexpected)
    with pytest.raises(ValueError, match='without tools'):
        await agent_runtime.generate_chat_json('system', [], 'Sample', tools=[object()],
                                               response_schema={'type': 'object'})
