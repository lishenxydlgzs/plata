"""Real Strands/AG-UI runs with deterministic model events and disposable data."""
import json

import pytest
from ag_ui.core import RunAgentInput, UserMessage, EventType
from strands.models.model import Model
from agent_server.agent_stream import LogbookRun
from agent_server.knowledge import KnowledgeStore
from agent_server.journal import JournalService
from agent_server.logbook_chat import ChatRequest, LogbookChat, ToolCall


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


def diagnostic_events(caplog):
    return [json.loads(r.message.removeprefix('logbook_run ')) for r in caplog.records
            if r.name == 'agent_server.run_diagnostics']


@pytest.mark.asyncio
async def test_diagnostics_explain_quote_failure_and_recovery_without_content(chat, caplog):
    caplog.set_level('INFO', logger='agent_server.run_diagnostics')
    run, body = make_run(chat)
    model = ScriptedModel([
        {'name': 'stage_note_changes', 'input': {'changes': [note_change(
            run.prepared['source_id'], 'Private invented quote.')] }},
        {'name': 'stage_note_changes', 'input': {'changes': [note_change(run.prepared['source_id'])]}},
        'A private response.',
    ])
    events = [e async for e in run.events(body, model=model)]
    assert events[-1].type == EventType.RUN_FINISHED
    logs = diagnostic_events(caplog)
    failure = next(e for e in logs if e.get('status') == 'error')
    assert failure['tool'] == 'stage_note_changes'
    assert failure['reason'] == 'quote_mismatch'
    assert failure['round'] == 1
    assert logs[-1]['committed'] is True
    assert logs[-1]['writes'] == 1
    assert all(e['request_id'] == run.request.request_id and e['run_id'] == body.run_id for e in logs)
    assert 'Private invented quote' not in json.dumps(logs)
    assert run.request.text not in json.dumps(logs)
    assert 'A private response' not in json.dumps(logs)


@pytest.mark.asyncio
async def test_diagnostics_capture_sdk_validation_before_tool_body(chat, caplog):
    caplog.set_level('INFO', logger='agent_server.run_diagnostics')
    run, body = make_run(chat)
    with pytest.raises(ValueError):
        _ = [e async for e in run.events(body, model=ScriptedModel([
            {'name': 'stage_note_changes', 'input': {'changes': 'Private invalid value.'}}, 'Saved.']))]
    logs = diagnostic_events(caplog)
    failure = next(e for e in logs if e.get('status') == 'error')
    assert failure['reason'] == 'schema_validation'
    assert failure['validation'][0]['path'] == ['changes']
    assert logs[-1]['committed'] is False
    assert 'Private invalid value' not in json.dumps(logs)


@pytest.mark.asyncio
async def test_diagnostics_identify_round_limit_and_no_commit(chat, caplog):
    caplog.set_level('INFO', logger='agent_server.run_diagnostics')
    run, body = make_run(chat)
    events = [e async for e in run.events(body, model=ScriptedModel([
        {'name': 'list_notes', 'input': {}} for _ in range(4)]))]
    assert events[-1].type == EventType.RUN_ERROR
    logs = diagnostic_events(caplog)
    assert any(e.get('reason') == 'round_limit' for e in logs)
    assert logs[-1]['committed'] is False
    assert chat.session(body.thread_id)['pending']
    assert len([e for e in logs if e['event'] == 'tool_completed']) == 4


def test_diagnostic_error_redacts_unknown_fields_and_exception_text():
    from pydantic import ValidationError
    from agent_server.logbook_chat import ToolCall
    from agent_server.run_diagnostics import error_details
    with pytest.raises(ValidationError) as caught:
        ToolCall.model_validate({'name': 'create_note', 'arguments': {}, 'Private field': 'Private value'})
    details = error_details(caught.value)
    assert details['validation'][0]['path'] == ['<field>']
    assert 'Private' not in json.dumps(details)
    assert error_details(ValueError('Private exception text')) == {
        'error_type': 'ValueError', 'reason': 'unclassified'}


@pytest.mark.asyncio
async def test_staging_schema_excludes_reads_and_recovers_from_mixed_batch(chat, caplog):
    caplog.set_level('INFO', logger='agent_server.run_diagnostics')
    initial, body = make_run(chat)
    chat.complete(body.thread_id, initial.request, initial.prepared, 'Recorded.',
                  [ToolCall.model_validate(note_change(initial.prepared['source_id']))])
    note_id = chat.journals.list()[0]['id']
    request = ChatRequest(text='Teamwork made it easier.', request_id='follow-up', selected_note_id=note_id)
    prepared = chat.prepare(body.thread_id, request)
    run = LogbookRun(chat, body.thread_id, request, prepared)
    schema = run.tools()[-1].tool_spec['inputSchema']['json']
    names = schema['$defs']['NoteWriteCall']['properties']['name']['enum']
    assert set(names) == {'create_note', 'update_note', 'delete_note', 'restore_note', 'set_note_visibility'}
    assert schema['$defs']['NoteWriteCall']['properties']['arguments']['$ref'] == '#/$defs/NoteWriteArguments'
    assert {'observations', 'parking_lot', 'original_quotes'} <= set(schema['$defs']['NoteContent']['required'])
    assert schema['$defs']['NoteContent']['properties']['original_quotes']['items']['$ref'] == '#/$defs/Quote'
    assert set(schema['$defs']['Quote']['required']) == {'message_id', 'text'}
    change = note_change(initial.prepared['source_id'])
    change['name'] = 'update_note'
    change['arguments']['note_id'] = note_id
    change['arguments']['content']['author_reflections'] = request.text
    events = [e async for e in run.events(body, model=ScriptedModel([
        {'name': 'stage_note_changes', 'input': {'changes': [
            {'name': 'read_note', 'arguments': {'note_id': note_id}}, change]}},
        {'name': 'read_note', 'input': {'note_id': note_id}},
        {'name': 'stage_note_changes', 'input': {'changes': [change]}},
        'Your reflection is included.',
    ]))]
    assert events[-1].type == EventType.RUN_FINISHED
    failures = [e for e in diagnostic_events(caplog) if e.get('status') == 'error']
    assert len(failures) == 1
    assert failures[0]['reason'] == 'schema_validation'
    assert failures[0]['operations'] == ['read_note', 'update_note']
    assert failures[0]['validation'][0]['path'] == ['changes', 0, 'name']
    assert len(chat.journals.get(note_id)['revisions']) == 2
    assert chat.session(body.thread_id)['pending'] is None


@pytest.mark.asyncio
@pytest.mark.parametrize('malformation', ['missing_field', 'quote_strings', 'misplaced_note_id'])
async def test_typed_note_contract_rejects_malformed_content_then_recovers(chat, caplog, malformation):
    caplog.set_level('INFO', logger='agent_server.run_diagnostics')
    run, body = make_run(chat)
    invalid = note_change(run.prepared['source_id'])
    if malformation == 'missing_field':
        del invalid['arguments']['content']['parking_lot']
    elif malformation == 'quote_strings':
        invalid['arguments']['content']['original_quotes'] = ['Private quotation.']
    else:
        invalid['note_id'] = 'Private misplaced ID'
    events = [e async for e in run.events(body, model=ScriptedModel([
        {'name': 'stage_note_changes', 'input': {'changes': [invalid]}},
        {'name': 'stage_note_changes', 'input': {'changes': [note_change(run.prepared['source_id'])]}},
        'Recorded.',
    ]))]
    assert events[-1].type == EventType.RUN_FINISHED
    logs = diagnostic_events(caplog)
    errors = [e for e in logs if e.get('status') == 'error']
    assert len(errors) == 1 and errors[0]['reason'] == 'schema_validation'
    assert 'Private' not in json.dumps(logs)
    assert len(chat.journals.list()) == 1
    assert len(chat.journals.get(chat.journals.list()[0]['id'])['revisions']) == 1


@pytest.mark.asyncio
async def test_typed_optional_arguments_preserve_delete_restore_and_sharing(chat):
    initial, body = make_run(chat)
    chat.complete(body.thread_id, initial.request, initial.prepared, 'Recorded.',
                  [ToolCall.model_validate(note_change(initial.prepared['source_id']))])
    note_id = chat.journals.list()[0]['id']
    for operation, text, extra in [
        ('delete_note', 'Remove this note.', {}),
        ('restore_note', 'Restore this note.', {}),
        ('set_note_visibility', 'Share this note with the family.', {'visibility': 'family'}),
    ]:
        request = ChatRequest(text=text, request_id=operation, selected_note_id=note_id)
        prepared = chat.prepare(body.thread_id, request)
        run = LogbookRun(chat, body.thread_id, request, prepared)
        events = [e async for e in run.events(body, model=ScriptedModel([
            {'name': 'stage_note_changes', 'input': {'changes': [
                {'name': operation, 'arguments': {'note_id': note_id, **extra}}]}}, 'Done.',
        ]))]
        assert events[-1].type == EventType.RUN_FINISHED
        note = chat.journals.get(note_id)
        assert note['archived'] == (operation == 'delete_note')
    assert note['visibility'] == 'family'
