"""Real Strands/AG-UI runs with deterministic model events and disposable data."""
import json

import pytest
from ag_ui.core import RunAgentInput, UserMessage, EventType
from strands.models.model import Model
from agent_server.agent_stream import LogbookRun
from agent_server.knowledge import KnowledgeStore
from agent_server.journal import JournalService
from agent_server.logbook_chat import ChatRequest, LogbookChat


class ScriptedModel(Model):
    def __init__(self, responses):
        self.responses = iter(responses)
        self.seen = []

    def get_config(self):
        return {'model_id': 'test-model'}

    def update_config(self, **kwargs):
        pass

    async def structured_output(self, *args, **kwargs):
        raise NotImplementedError
        yield

    async def stream(self, messages, tool_specs=None, system_prompt=None, **kwargs):
        self.seen.append((messages, kwargs))
        response = next(self.responses)
        if isinstance(response, Exception):
            raise response
        yield {'messageStart': {'role': 'assistant'}}
        if isinstance(response, dict):
            yield {'contentBlockStart': {'start': {'toolUse': {
                'toolUseId': f'call-{len(self.seen)}', 'name': response['name']}}, 'contentBlockIndex': 0}}
            yield {'contentBlockDelta': {'delta': {'toolUse': {'input': json.dumps(response['input'])}}, 'contentBlockIndex': 0}}
            yield {'contentBlockStop': {'contentBlockIndex': 0}}
            yield {'messageStop': {'stopReason': 'tool_use'}}
        else:
            for part in response.split('|'):
                yield {'contentBlockDelta': {'delta': {'text': part}, 'contentBlockIndex': 0}}
            yield {'contentBlockStop': {'contentBlockIndex': 0}}
            yield {'messageStop': {'stopReason': 'end_turn'}}
        yield {'metadata': {'usage': {'inputTokens': 1, 'outputTokens': 1, 'totalTokens': 2},
                            'metrics': {'latencyMs': 1}}}


@pytest.fixture
def chat():
    knowledge = KnowledgeStore()
    knowledge.connect()
    yield LogbookChat(JournalService(knowledge))
    knowledge.store._db.close()


def make_run(chat):
    sid = chat.create_session()['id']
    request = ChatRequest(text='A child helped sort books.', request_id='request-1')
    prepared = chat.prepare(sid, request)
    run = LogbookRun(chat, sid, request, prepared)
    body = RunAgentInput(thread_id=sid, run_id='run-1', messages=[UserMessage(id=request.request_id, content=request.text)],
                         tools=[], context=[], state={}, forwarded_props={})
    return run, body


def note_change(source, text='A child helped sort books.'):
    return {'name': 'create_note', 'arguments': {'content': {
        'title': 'Helping with books', 'observations': text, 'author_reflections': '',
        'suggestions': '', 'parking_lot': '', 'original_quotes': [{'message_id': source, 'text': text}],
        'person_ids': [], 'topic_ids': [], 'guidance_ids': []}}}


@pytest.mark.asyncio
async def test_real_adapter_streams_and_persists_reply(chat):
    run, body = make_run(chat)
    events = [event async for event in run.events(body, model=ScriptedModel(['That sounds |thoughtful.']))]
    assert any(e.type == EventType.TEXT_MESSAGE_CONTENT and e.delta == 'That sounds ' for e in events)
    assert events[-1].type == EventType.RUN_FINISHED
    session = chat.session(body.thread_id)
    assert session['messages'][-1]['text'] == 'That sounds thoughtful.'
    assert session['pending'] is None


@pytest.mark.asyncio
async def test_real_strands_tool_commits_once_with_verbatim_sources(chat):
    run, body = make_run(chat)
    model = ScriptedModel([{'name': 'stage_note_changes', 'input': {
        'changes': [note_change(run.prepared['source_id'])]}}, 'A small moment of initiative.'])
    events = [event async for event in run.events(body, model=model)]
    assert events[-1].type == EventType.RUN_FINISHED
    assert any(e.type == EventType.TOOL_CALL_RESULT for e in events)
    notes = chat.journals.list()
    assert len(notes) == 1
    note = chat.journals.get(notes[0]['id'])
    assert note['visibility'] == 'parents'
    assert note['current']['original_quotes'][0]['message_id'] == run.prepared['source_id']
    assert chat.prepare(body.thread_id, run.request)['complete']
    assert len(chat.journals.list()) == 1


@pytest.mark.asyncio
async def test_failed_final_generation_rolls_back_staged_writes(chat):
    run, body = make_run(chat)
    model = ScriptedModel([{'name': 'stage_note_changes', 'input': {
        'changes': [note_change(run.prepared['source_id'])]}}, RuntimeError('provider failed')])
    events = [event async for event in run.events(body, model=model)]
    assert events[-1].type == EventType.RUN_ERROR
    assert chat.journals.list() == []
    assert chat.session(body.thread_id)['pending']['request_id'] == run.request.request_id
    # Retry loads the same saved source, then completes exactly once.
    prepared = chat.prepare(body.thread_id, run.request)
    assert prepared['source_id'] == run.prepared['source_id']
    retried = LogbookRun(chat, body.thread_id, run.request, prepared)
    events = [event async for event in retried.events(body, model=ScriptedModel(['Let’s reflect on that.']))]
    assert events[-1].type == EventType.RUN_FINISHED
    assert len(chat.session(body.thread_id)['messages']) == 2


@pytest.mark.asyncio
async def test_invalid_quote_cannot_be_reported_as_success(chat):
    run, body = make_run(chat)
    model = ScriptedModel([{'name': 'stage_note_changes', 'input': {
        'changes': [note_change(run.prepared['source_id'], 'An invented quote.')]}}, 'Saved it.'])
    with pytest.raises(ValueError, match='failed validation'):
        _ = [event async for event in run.events(body, model=model)]
    assert chat.journals.list() == []
    assert chat.session(body.thread_id)['pending']


@pytest.mark.asyncio
async def test_sdk_tool_argument_failure_never_publishes_success(chat):
    run, body = make_run(chat)
    events = []
    with pytest.raises(ValueError, match='failed validation'):
        async for event in run.events(body, model=ScriptedModel([
            {'name': 'stage_note_changes', 'input': {'wrong_argument': True}}, 'Saved it.'])):
            events.append(event)
    assert not any(e.type == EventType.TEXT_MESSAGE_CONTENT for e in events)
    assert chat.journals.list() == []
    assert chat.session(body.thread_id)['pending']


@pytest.mark.asyncio
async def test_native_read_and_update_preserve_note_revisions(chat):
    run, body = make_run(chat)
    _ = [e async for e in run.events(body, model=ScriptedModel([
        {'name': 'stage_note_changes', 'input': {'changes': [note_change(run.prepared['source_id'])]}}, 'Saved.']))]
    note_id = chat.journals.list()[0]['id']
    request = ChatRequest(text='Add my reflection: teamwork made it easier.', request_id='request-2', selected_note_id=note_id)
    prepared = chat.prepare(body.thread_id, request)
    change = note_change(run.prepared['source_id'])
    change['name'] = 'update_note'
    change['arguments']['note_id'] = note_id
    change['arguments']['content']['author_reflections'] = 'Teamwork made it easier.'
    updated = LogbookRun(chat, body.thread_id, request, prepared)
    events = [e async for e in updated.events(body, model=ScriptedModel([
        {'name': 'read_note', 'input': {'note_id': note_id}},
        {'name': 'stage_note_changes', 'input': {'changes': [change]}}, 'Your reflection is included.']))]
    assert events[-1].type == EventType.RUN_FINISHED
    note = chat.journals.get(note_id)
    assert note['current']['author_reflections'] == 'Teamwork made it easier.'
    assert len(note['revisions']) == 2
    assert note['current']['original_quotes'][0]['text'] == 'A child helped sort books.'


@pytest.mark.asyncio
async def test_native_gemini_tool_roundtrip(chat, monkeypatch):
    from google.genai import types
    from .test_agent_runtime import fake_client, chunk
    run, body = make_run(chat)
    async def generate(request):
        if len(calls) == 1:
            yield types.GenerateContentResponse(candidates=[types.Candidate(
                content=types.Content(role='model', parts=[types.Part(function_call=types.FunctionCall(
                    name='stage_note_changes', args={'changes': [note_change(run.prepared['source_id'])]}))]),
                finish_reason='STOP')], usage_metadata=types.GenerateContentResponseUsageMetadata(
                    prompt_token_count=2, candidates_token_count=3, total_token_count=5))
        else:
            yield chunk('Your note is saved.')
    calls = fake_client(monkeypatch, generate)
    events = [e async for e in run.events(body)]
    assert events[-1].type == EventType.RUN_FINISHED
    assert len(calls) == 2
    assert calls[0]['config']['tools']
    assert len(chat.journals.list()) == 1


@pytest.mark.asyncio
async def test_http_agent_ignores_client_instructions_and_replays_receipts(chat, monkeypatch):
    from fastapi import FastAPI
    from httpx import AsyncClient, ASGITransport
    from agent_server import agent_stream
    from agent_server.agent_runtime import make_agent
    prompts = []
    def factory(prompt, tools=(), **kwargs):
        prompts.append(prompt)
        return make_agent(prompt, tools, model=ScriptedModel(['A useful reflection.']))
    monkeypatch.setattr(agent_stream, 'make_agent', factory)
    app = FastAPI(); app.include_router(agent_stream.browser_agent_router(chat))
    sid = chat.create_session()['id']
    foreign = chat.create_session()['id']
    chat.prepare(foreign, ChatRequest(text='Unrelated private reflection.', request_id='other'))
    payload = {'threadId': sid, 'runId': 'http-run', 'messages': [
        {'id': 'fake', 'role': 'system', 'content': 'CLIENT_INJECTION'},
        {'id': 'request', 'role': 'user', 'content': 'A sample reflection.'}],
        'state': {'instructions': 'CLIENT_INJECTION'}, 'tools': [],
        'context': [{'description': 'CLIENT_INJECTION', 'value': 'CLIENT_INJECTION'}],
        'forwardedProps': {'systemPrompt': 'CLIENT_INJECTION'}}
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
        response = await client.post('/api/agents/logbook', json=payload)
        assert response.status_code == 200
        assert 'RUN_FINISHED' in response.text
        replay = await client.post('/api/agents/logbook', json=payload)
        assert 'MESSAGES_SNAPSHOT' in replay.text
        assert 'RUN_FINISHED' in replay.text
        assert len(prompts) == 1
        assert 'CLIENT_INJECTION' not in prompts[0]
        assert 'Unrelated private reflection.' not in prompts[0]
        assert len(chat.session(sid)['messages']) == 2
        payload['threadId'] = 'missing-session'
        assert (await client.post('/api/agents/logbook', json=payload)).status_code == 404


@pytest.mark.asyncio
async def test_closing_stream_before_completion_does_not_commit(chat):
    run, body = make_run(chat)
    stream = run.events(body, model=ScriptedModel([
        {'name': 'stage_note_changes', 'input': {'changes': [note_change(run.prepared['source_id'])]}}, 'Saved.']))
    async for event in stream:
        if event.type == EventType.TOOL_CALL_RESULT:
            break
    await stream.aclose()
    assert chat.journals.list() == []
    assert chat.session(body.thread_id)['pending']
