"""Parent authorization and atomic native note contracts; no live services."""
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from agent_server.household import PersonRequest, PersonRoleRequest
from agent_server.knowledge import KnowledgeStore
from agent_server.models import ConversationRequest
from agent_server.telegram_music import TelegramMusicContext, music_context
from agent_server.telegram_notes import TelegramNotes
from .test_telegram import service, pair, receive, update
from .test_logbook_chat import content


@pytest.fixture
async def parent(service):
    knowledge = KnowledgeStore(); knowledge.connect()
    person = knowledge.create_person(PersonRequest(name='Sample Parent', role='parent'))
    service.knowledge = knowledge
    service.router._db = SimpleNamespace(note_sources=AsyncMock(return_value=[
        {'id': 'earlier', 'text': 'A child helped sort books.'}]))
    await pair(service, person=person['id'])
    account = service.db.execute('SELECT * FROM accounts').fetchone()
    context = TelegramMusicContext(service, 101, 101, person['id'], account['conversation_id'], account['grant_id'], 10)
    binding = music_context.set(context)
    request = ConversationRequest(text='Please save that observation as a note.', source='telegram',
                                  conversation_id=context.conversation_id, person_id=person['id'])
    yield service, context, request
    music_context.reset(binding)
    knowledge.store._db.close()


def turn(parent):
    service, context, request = parent
    notes = TelegramNotes(request, service.knowledge)
    return notes, {t.tool_name: t for t in notes.tools()}


async def stage(parent):
    notes, tools = turn(parent)
    listing = await tools['list_notes']()
    source = listing['sources'][0]
    result = await tools['create_note'](content=content(source['id'], source['text']))
    assert result['status'] == 'staged'
    return notes, tools


async def test_create_is_atomic_private_source_backed_and_replay_safe(parent):
    service, context, request = parent
    notes, tools = await stage(parent)
    assert notes.service.journals.list() == []
    assert notes.commit('Saved the observation.') == 'Saved the observation.'
    note = notes.service.journals.get(notes.service.journals.list()[0]['id'])
    assert note['visibility'] == 'parents' and len(note['revisions']) == 1
    assert notes.service._sources(note['current'])[0]['text'] == 'A child helped sort books.'
    replay, _ = await stage(parent)
    assert replay.commit('Different regenerated reply') == 'Saved the observation.'
    assert len(replay.service.journals.list()) == 1


async def test_parent_lists_and_reads_private_notes_then_updates(parent):
    notes, tools = await stage(parent); notes.commit('Saved.')
    id = notes.service.journals.list()[0]['id']
    service, context, request = parent
    binding = music_context.set(replace(context, update_id=11))
    try:
        edited, tools = turn(parent)
        note = await tools['read_note'](note_id=id)
        assert note['visibility'] == 'parents'
        current = note['current']
        changed = {k: current[k] for k in ('title', 'observations', 'author_reflections', 'suggestions',
                   'parking_lot', 'original_quotes', 'person_ids', 'topic_ids', 'guidance_ids')}
        changed['suggestions'] = 'Try a shared sorting activity.'
        assert (await tools['update_note'](note_id=id, content=changed))['status'] == 'staged'
        edited.commit('Updated.')
        assert len(edited.service.journals.get(id)['revisions']) == 2
    finally:
        music_context.reset(binding)


async def test_invalid_quote_prevents_all_staged_writes(parent):
    notes, tools = await stage(parent)
    assert 'error' in await tools['create_note'](content=content('forged-source'))
    with pytest.raises(ValueError): notes.commit('Saved.')
    assert notes.service.journals.list() == []


async def test_no_commit_without_valid_final_reply(parent):
    notes, _ = await stage(parent)
    with pytest.raises(ValueError): notes.commit('')
    assert notes.service.journals.list() == []


@pytest.mark.parametrize('change', ['role', 'revoke', 'context', 'conversation'])
async def test_authority_rechecked_after_staging(parent, change):
    service, context, request = parent
    notes, tools = await stage(parent)
    binding = None
    if change == 'role': service.knowledge.set_person_role(context.person_id, 'child')
    elif change == 'revoke': service.revoke(context.user_id)
    elif change == 'context': binding = music_context.set(replace(context, grant_id='forged'))
    else: service.db.execute('UPDATE accounts SET conversation_id=?', ('different',)); service.db.commit()
    try:
        assert 'error' in await tools['list_notes']()
        with pytest.raises(ValueError): notes.commit('Saved.')
        assert notes.service.journals.list() == []
    finally:
        if binding: music_context.reset(binding)


async def test_robot_and_forged_sender_get_no_note_tools(parent):
    service, context, request = parent
    for changes in ({'source': 'assist'}, {'person_id': 'other'}, {'conversation_id': 'other'}):
        assert TelegramNotes(request.model_copy(update=changes), service.knowledge).tools() == []


async def test_current_note_version_required_at_commit(parent):
    notes, _ = await stage(parent); notes.commit('Saved.')
    id = notes.service.journals.list()[0]['id']
    binding = music_context.set(replace(parent[1], update_id=12))
    try:
        edited, tools = turn(parent)
        await tools['read_note'](note_id=id)
        data = content(next(iter(edited.allowed)), next(iter(edited.allowed.values())))
        assert (await tools['update_note'](note_id=id, content=data))['status'] == 'staged'
        edited.service.db.execute('UPDATE entities SET updated_at=? WHERE id=?', ('changed', id)); edited.service.db.commit()
        with pytest.raises(ValueError): edited.commit('Updated.')
        assert len(edited.service.journals.get(id)['revisions']) == 1
    finally: music_context.reset(binding)


async def test_roles_default_unassigned_and_everyone_can_be_invited(parent):
    service, context, request = parent
    for role in (None, 'child'):
        person = service.knowledge.create_person(PersonRequest(name='Sample Person', role=role))
        assert person['role'] == role
        assert service.invite(person['id'])['url']
    with pytest.raises(ValueError): PersonRoleRequest(role='administrator')


async def test_role_change_does_not_block_pairing(parent):
    service, _, _ = parent
    person = service.knowledge.create_person(PersonRequest(name='Other Parent', role='parent'))
    invitation = service.invite(person['id'])
    await receive(service, update(22, '/start ' + invitation['url'].split('=')[1], user=202))
    service.knowledge.set_person_role(person['id'], 'child')
    service.approve(invitation['id'])
    assert service.db.execute('SELECT 1 FROM accounts WHERE user_id=202').fetchone()


async def test_role_api_boundary_and_demotion_preserve_pairing_retire_private_context(parent, monkeypatch):
    from agent_server import app as application
    service, context, request = parent
    monkeypatch.setattr(application, 'knowledge_store', service.knowledge)
    monkeypatch.setattr(application, 'telegram', service)
    url = f'/api/people/{context.person_id}/role'
    async with httpx.AsyncClient(transport=httpx.ASGITransport(application.app), base_url='http://test') as client:
        assert (await client.patch(url, json={'role': 'parent'})).status_code == 403
        headers = {'X-Plata-Workspace': '1'}
        assert (await client.patch(url, json={'role': 'child'}, headers={**headers, 'Origin': 'https://evil.invalid'})).status_code == 403
        response = await client.patch(url, json={'role': 'child'}, headers=headers)
        assert response.status_code == 200 and response.json()['role'] == 'child'
        account = service.db.execute('SELECT * FROM accounts').fetchone()
        assert account['user_id'] == context.user_id
        assert account['conversation_id'] != context.conversation_id
        assert account['grant_id'] != context.grant_id
        assert not service.db.execute('SELECT 1 FROM outbox').fetchone()


async def test_native_note_tools_and_json_recovery_commit_once(parent, monkeypatch):
    import uuid
    from google.genai import types
    from agent_server.modes.chat import ChatHandler
    from .test_agent_runtime import fake_client, chunk
    service, context, request = parent
    source_id = str(uuid.uuid5(uuid.NAMESPACE_URL,
        f'telegram:{context.grant_id}:{context.conversation_id}:earlier'))
    def call(name, arguments):
        response = chunk('')
        response.candidates[0].content = types.Content(role='model', parts=[
            types.Part(function_call=types.FunctionCall(name=name, args=arguments))])
        return response
    async def generate(provider_request):
        if len(calls) == 1: yield call('list_notes', {})
        elif len(calls) == 2:
            yield call('create_note', {'content': content(source_id)})
        elif len(calls) == 3:
            assert TelegramNotes(request, service.knowledge).service.journals.list() == []
            yield chunk('The observation is ready to save.')
        else:
            assert not provider_request['config'].get('tools')
            yield chunk('{"reply_text":"Saved your observation as a note.","learning_events":[],"behavior_events":[],"facts":[]}')
    calls = fake_client(monkeypatch, generate)
    response = await ChatHandler(service.knowledge).handle(request, [])
    assert response.reply_text == 'Saved your observation as a note.'
    assert len(calls) == 4
    notes = TelegramNotes(request, service.knowledge).service.journals.list()
    assert len(notes) == 1 and notes[0]['visibility'] == 'parents'
    assert not service.knowledge.get_events('learning_event')


async def test_nonparent_existing_pairing_keeps_ordinary_chat(parent):
    service, context, _ = parent
    service.knowledge.set_person_role(context.person_id, 'child')
    await receive(service, update(100, 'List the notes'))
    service.router.route.assert_awaited_once()
    await service.flush()
    assert any('rainy-day map' in message['text'] for message in service.api.sent)


async def test_queued_reply_from_retired_context_is_dropped(parent):
    service, context, _ = parent
    await service.flush(); service.api.sent.clear()
    service.queue(101, 'Synthetic private note response', grant_id=context.grant_id)
    service.db.commit()
    service.db.execute("UPDATE accounts SET grant_id='retired-context'"); service.db.commit()
    await service.flush()
    assert service.api.sent == []


async def test_source_history_excludes_other_conversations_and_assistant_text():
    from agent_server.context import ConversationDB
    db = ConversationDB(); await db.connect()
    try:
        await db.save_turn('sample-chat', 'Sample observation', 'Assistant suggestion')
        await db.save_turn('another-chat', 'Other observation', 'Other suggestion')
        sources = await db.note_sources('sample-chat')
        assert [s['text'] for s in sources] == ['Sample observation']
        assert sources[0]['id']
    finally: await db.close()


async def test_nonparent_ontology_search_cannot_read_even_shared_notes(parent):
    from agent_server.ontology_search import search_tool
    from agent_server.journal import JournalPatch
    notes, _ = await stage(parent); notes.commit('Saved.')
    id = notes.service.journals.list()[0]['id']
    notes.service.journals.patch(id, JournalPatch(visibility='family'))
    lookup = search_tool(parent[0].knowledge, notes_allowed=notes.authorized)
    assert (await lookup(entity_types=['journal']))['records']
    parent[0].knowledge.set_person_role(parent[1].person_id, 'child')
    assert (await lookup(entity_types=['journal']))['records'] == []


async def test_note_tool_results_do_not_enter_shared_memory(parent, monkeypatch):
    from agent_server.modes import chat
    async def generate(prompt, history, text, **kwargs):
        tool = next(t for t in kwargs['tools'] if t.tool_name == 'list_notes')
        await tool()
        return {'reply_text': 'Here are the notes.', 'facts': [{'subject': 'Synthetic private material'}],
                'topics': ['private-topic'], 'learning_events': [], 'behavior_events': []}
    monkeypatch.setattr(chat, 'generate_chat_json', generate)
    response = await chat.ChatHandler(parent[0].knowledge).handle(parent[2], [])
    assert response.reply_text == 'Here are the notes.'
    assert not parent[0].knowledge.store._db.execute("SELECT 1 FROM entities WHERE entity_type IN ('message','fact','topic')").fetchone()
