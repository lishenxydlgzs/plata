"""Search authorization/projection and native Strands tool contracts."""
import json

import pytest
from google.genai import types

from agent_server.knowledge import KnowledgeStore
from agent_server.household import PersonRequest, MemorySettings, GuidanceRequest
from agent_server.journal import JournalService, NewJournal, JournalPatch
from agent_server.ontology_search import OntologySearch, search_tool
from agent_server.models import ConversationRequest
from agent_server.modes.chat import ChatHandler
from agent_server import agent_runtime
from .test_agent_runtime import fake_client, chunk


@pytest.fixture
def store():
    store = KnowledgeStore()
    store.connect()
    yield store
    store.store._db.close()


def note(store, text, *, visibility='family', archived=False, person_id=None):
    journals = JournalService(store)
    journal = journals.create(NewJournal(title='Sample reflection'))
    journals.patch(journal['id'], JournalPatch(visibility=visibility, archived=archived))
    revision = journals._insert('reflection_revision', 'Sample reflection', {
        'journal_id': journal['id'], 'version': 1, 'observations': text,
        'author_reflections': '', 'suggestions': '', 'parking_lot': '',
        'chat_managed': True, 'source_entry_ids': [], 'original_quotes': [{'text': 'Excluded original quote'}]})
    if person_id:
        store.store.create_link('involves', revision, person_id)
    store.store._db.commit()
    return journal['id'], revision


def event(store, person, kind='learning_event', **changes):
    message = store.record_message('Private source transcript', 'source-session', [])
    report = {'person_id': person['id'], 'child_name': person['name'], 'summary': 'Practiced taking turns',
              'topic': 'cooperation', 'material': 'board game', 'outcome': 'practiced', **changes}
    return store.record_events(kind, [report], message, 'source-session', 'Private source transcript')[0]


def test_browse_multiple_people_and_follow_graph_links(store):
    people = [store.create_person(PersonRequest(name=name)) for name in ('Sample Child', 'Other Child')]
    events = [event(store, p) for p in people]
    journal, revision = note(store, 'Reported difficulty waiting for a turn.', person_id=people[1]['id'])
    search = OntologySearch(store)
    result = search.search(person_ids=[p['id'] for p in people], entity_types=['learning_event', 'journal'])
    assert {r['id'] for r in result['records']} == {journal, *(e['id'] for e in events)}
    assert any(l['source'] == journal and l['target'] == people[1]['id'] for l in result['links'])
    assert revision not in json.dumps(result)
    assert 'Private source transcript' not in json.dumps(result)
    assert 'source-session' not in json.dumps(result)
    assert search.search(entity_ids=[people[0]['id']])['records'][0]['type'] == 'person'


def test_private_archived_and_superseded_notes_cannot_leak_via_ids_text_or_edges(store):
    person = store.create_person(PersonRequest(name='Sample Person'))
    private, private_revision = note(store, 'private-marker', visibility='parents', person_id=person['id'])
    archived, _ = note(store, 'archived-marker', archived=True, person_id=person['id'])
    public, old_revision = note(store, 'superseded-marker', person_id=person['id'])
    journals = JournalService(store)
    new_revision = journals._insert('reflection_revision', 'Current sample reflection', {
        'journal_id': public, 'version': 2, 'observations': 'Current public observation',
        'chat_managed': True, 'source_entry_ids': []})
    store.store._db.commit()
    search = OntologySearch(store)
    serialized = json.dumps(search.search())
    for hidden in (private, private_revision, archived, old_revision, new_revision,
                   'private-marker', 'archived-marker', 'superseded-marker', 'Excluded original quote'):
        assert hidden not in serialized
        assert search.search(query=hidden)['records'] == []
    assert search.search(entity_ids=[private, private_revision, archived, old_revision])['records'] == []
    assert search.search(person_ids=[person['id']], entity_types=['journal'])['records'] == []
    assert search.search(query='Current public')['records'][0]['id'] == public
    journals.patch(public, JournalPatch(visibility='parents'))
    assert search.search(entity_ids=[public])['records'] == []


def test_memory_switches_apply_to_search_and_graph_links(store):
    person = store.create_person(PersonRequest(name='Sample Child'))
    learning, behavior = event(store, person), event(store, person, 'behavior_event')
    store.save_memory_settings(MemorySettings(behavior_logging=False, learning_logging=False))
    result = OntologySearch(store).search()
    assert learning['id'] not in json.dumps(result) and behavior['id'] not in json.dumps(result)
    store.save_memory_settings(MemorySettings(behavior_logging=False, learning_logging=True))
    assert OntologySearch(store).search(entity_ids=[learning['id']])['records']


def test_search_is_read_only_validated_and_paginates(store):
    for i in range(5):
        store.create_person(PersonRequest(name=f'Sample Person {i}'))
    search = OntologySearch(store)
    store.store._db.execute('PRAGMA query_only=ON')
    pages = [search.search(entity_types=['person'], limit=2, offset=i) for i in (0, 2, 4)]
    assert [p['next_offset'] for p in pages] == [2, 4, None]
    assert len({r['id'] for p in pages for r in p['records']}) == 5
    for args in ({'limit': 100}, {'limit': True}, {'offset': -1}, {'query': 'x'*301}, {'entity_types': ['journal_message']}):
        assert 'error' in search.search(**args)
    assert len(search.search(query="' OR 1=1 --")['records']) == 1
    assert search.search(person_ids=['unknown'])['records'] == []


def test_projection_rejects_arbitrary_nested_properties_and_inactive_guidance(store):
    person = store.create_person(PersonRequest(name='Sample Person'))
    store.store.update_entity(person['id'], properties={'aliases': ['Allowed alias', {'text':'nested-secret'}],
                                                       'secret': 'hidden-secret'})
    store.save_guidance_document(GuidanceRequest(document_key='sample', title='Superseded guidance', content='old-secret'))
    store.save_guidance_document(GuidanceRequest(document_key='sample', title='Current guidance', content='A current value'))
    result = json.dumps(OntologySearch(store).search())
    assert all(s not in result for s in ('nested-secret', 'hidden-secret', 'old-secret'))
    assert 'Allowed alias' in result and 'A current value' in result


async def test_tool_budget_is_per_turn(store):
    tool = search_tool(store)
    for _ in range(6):
        assert 'error' not in await tool()
    assert 'budget' in (await tool())['error']
    assert 'error' not in await search_tool(store)()


def function_call(arguments):
    return types.GenerateContentResponse(candidates=[types.Candidate(
        content=types.Content(role='model', parts=[types.Part(function_call=types.FunctionCall(
            name='search_ontology', args=arguments))]), finish_reason='STOP')],
        usage_metadata=types.GenerateContentResponseUsageMetadata(prompt_token_count=2, candidates_token_count=3, total_token_count=5))


async def test_real_strands_search_then_answer_over_multiple_children(store, monkeypatch):
    people = [store.create_person(PersonRequest(name=name)) for name in ('Sample Child', 'Other Child')]
    for p in people:
        event(store, p)
    note(store, 'A family-visible observation about sharing.', person_id=people[1]['id'])
    note(store, 'parent-only-marker', visibility='parents', person_id=people[0]['id'])
    async def generate(request):
        if len(calls) == 1:
            assert request['config']['tools']
            assert request['config'].get('response_mime_type') is None
            yield function_call({'person_ids': [p['id'] for p in people], 'entity_types': ['learning_event', 'journal']})
        else:
            content = json.dumps(request['contents'], ensure_ascii=False)
            assert 'function_response' in content
            assert 'family-visible observation' in content and 'parent-only-marker' not in content
            assert all(p['id'] in content for p in people)
            yield chunk('{"reply_text":"The available reports describe practice with taking turns; parent-only notes are unavailable here.","facts":[],"learning_events":[],"behavior_events":[]}')
    calls = fake_client(monkeypatch, generate)
    response = await ChatHandler(store).handle(ConversationRequest(text='What should our children work on?',
                                              conversation_id='telegram:sample', source='telegram'), [])
    assert response.reply_text.startswith('The available reports')
    assert response.actions == []
    assert len(calls) == 2
    assert len(store.get_events('learning_event')) == 2  # Retrieved reports were not saved as new reports.


async def test_tool_loop_has_a_model_round_limit(store, monkeypatch):
    async def generate(request):
        yield function_call({'entity_types': ['person']})
    calls = fake_client(monkeypatch, generate)
    with pytest.raises(Exception, match='too many tool rounds'):
        await agent_runtime.generate_chat_json('Return a JSON response.', [], 'Sample question', tools=[search_tool(store)])
    assert len(calls) == 4


async def test_tool_final_json_fence_is_accepted(store, monkeypatch):
    async def generate(request):
        yield chunk('```json\n{"reply_text":"A sample reply"}\n```')
    calls = fake_client(monkeypatch, generate)
    result = await agent_runtime.generate_chat_json('Reply with JSON.', [], 'Hello', tools=[search_tool(store)])
    assert result['reply_text'] == 'A sample reply' and len(calls) == 1
