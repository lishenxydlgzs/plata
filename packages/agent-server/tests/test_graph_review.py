"""Graph corrections use synthetic reports; never deployed household data."""
import pytest
from httpx import AsyncClient, ASGITransport

from agent_server.app import app, knowledge_store, conversation_db
from agent_server import graph_review
from agent_server.household import PersonRequest


@pytest.fixture
async def client():
    knowledge_store.connect()
    await conversation_db.connect()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    knowledge_store.store._db.close()
    await conversation_db.close()


async def submit(client, text):
    session = (await client.post('/api/graph/review-sessions', json={})).json()['id']
    return await client.post(f'/api/graph/review-sessions/{session}/messages', json={'text':text})


@pytest.mark.parametrize('result', [None, [], {'reply_text':'Okay', 'actions':{}}, {'reply_text':''}])
async def test_invalid_model_response_is_retryable_json(client, monkeypatch, result):
    async def fake(*args):
        return result
    monkeypatch.setattr(graph_review, 'generate_chat_json', fake)
    response = await submit(client, 'Review my records')
    assert response.status_code == 503
    assert 'No changes were applied' in response.json()['detail']


async def test_upstream_error_is_retryable_json(client, monkeypatch):
    async def fake(*args):
        raise RuntimeError('private upstream detail')
    monkeypatch.setattr(graph_review, 'generate_chat_json', fake)
    response = await submit(client, 'Review my records')
    assert response.status_code == 503
    assert 'private upstream detail' not in response.text


async def test_correct_event_relinks_person_and_preserves_source(client, monkeypatch):
    store=knowledge_store
    message=store.record_message('A sample report', 'synthetic', [])
    original=store.record_events('behavior_event', [{'child_name':'Sample One','summary':'Sample One pushed a sibling'}], message, 'synthetic', 'A sample report')[0]
    person=store.create_person(PersonRequest(name='Sample Two'))
    async def fake(prompt, history, text):
        assert original['id'] in prompt and person['id'] in prompt
        assert history == []
        return {'reply_text':'Corrected.', 'actions':[{'type':'correct_record','id':original['id'],
                'corrected_text':'Sample Two pushed a sibling','person_id':person['id']}]}
    monkeypatch.setattr(graph_review,'generate_chat_json',fake)
    result=await submit(client,'Correct the child in this report to Sample Two')
    assert result.json()['actions'][0]['applied']
    entity=store.store.get_entity(original['id'])
    assert entity.properties['child_name']=='Sample Two'
    assert entity.properties['source_text']=='A sample report'
    assert entity.properties['corrections'][0]['previous_person_id']==original['person_id']
    links=store.store.get_entity_links(entity.id)
    assert [l.to_entity for l in links if l.relationship_type=='involves']==[person['id']]
    assert any(l.relationship_type=='reports' and l.from_entity==message for l in links)


async def test_message_correction_preserves_original_transcription(client, monkeypatch):
    message=knowledge_store.record_message('Sample transcription', 'synthetic', [])
    async def fake(*args):
        return {'reply_text':'Corrected.', 'actions':[{'type':'correct_record','id':message,'corrected_text':'Corrected transcription'}]}
    monkeypatch.setattr(graph_review,'generate_chat_json',fake)
    response=await submit(client,f'Correct record {message}')
    assert response.json()['actions'][0]['applied']
    props=knowledge_store.store.get_entity(message).properties
    assert props['text']=='Sample transcription'
    assert props['corrected_text']=='Corrected transcription'
    assert len(props['corrections'])==1


async def test_invalid_identity_does_not_partially_change_record(client, monkeypatch):
    message=knowledge_store.record_message('Sample report','synthetic',[])
    async def fake(*args):
        return {'reply_text':'I changed it.', 'actions':[{'type':'correct_record','id':message,'corrected_text':'Wrong change','person_id':'unknown'}]}
    monkeypatch.setattr(graph_review,'generate_chat_json',fake)
    response=await submit(client,'Correct the record')
    assert not response.json()['actions'][0]['applied']
    assert 'could not be applied' in response.json()['reply_text']
    assert 'corrected_text' not in knowledge_store.store.get_entity(message).properties


async def test_explicit_old_record_included(client, monkeypatch):
    old=knowledge_store.record_message('Old sample record','old',[])
    for i in range(35):
        knowledge_store.record_message(f'New sample record {i}', 'new', [])
    async def fake(prompt, *args):
        assert old in prompt
        return {'reply_text':'Found the old record.', 'actions':[]}
    monkeypatch.setattr(graph_review,'generate_chat_json',fake)
    assert (await submit(client,f'Review record {old}')).status_code==200


async def test_learning_identity_correction_clears_old_session(client, monkeypatch):
    store=knowledge_store
    message=store.record_message('Started a sample lesson','synthetic',[])
    event=store.record_events('learning_event',[{'child_name':'Sample One','summary':'Started lesson',
        'topic':'alphabet','material':'alphabet','outcome':'started'}],message,'synthetic','Started a sample lesson')[0]
    person=store.create_person(PersonRequest(name='Sample Two'))
    async def fake(*args):
        return {'reply_text':'Corrected.', 'actions':[{'type':'correct_record','id':event['id'],
                'corrected_text':'Sample Two started the lesson','person_id':person['id']}]}
    monkeypatch.setattr(graph_review,'generate_chat_json',fake)
    response=await submit(client,'The learner was Sample Two')
    assert response.json()['actions'][0]['applied']
    props=store.store.get_entity(event['id']).properties
    assert props['session_id'] is None
    assert props['material']=='alphabet' and props['outcome']=='started'
    assert props['corrections'][0]['previous_session_id']==event['id']
