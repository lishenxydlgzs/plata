import json
from unittest.mock import AsyncMock
import pytest
from agent_server.knowledge import KnowledgeStore
from agent_server.journal import JournalService, NewJournal, EntryRequest, JournalPatch


@pytest.fixture
def service():
    k = KnowledgeStore(); k.connect()
    yield JournalService(k)
    k.store._db.close()


def note(**changes):
    return dict(title='Helping at home', observations='Sample Person helped sort books.',
                author_reflections='I appreciated the contribution.', suggestions='',
                parking_lot='', reply='I recorded your observation.', person_ids=[],
                topic_ids=[], guidance_ids=[], **changes)


@pytest.mark.asyncio
async def test_preserves_sources_revisions_links_and_scopes(service, monkeypatch):
    person=service.knowledge.store.create_entity('person','Sample Person')
    model=AsyncMock(return_value={**note(),'person_ids':[person.id]})
    monkeypatch.setattr('agent_server.journal.generate_chat_json',model)
    j=service.create(NewJournal())
    entry=EntryRequest(text='Sample Person helped sort books.',request_id='first')
    service.append(j['id'],entry);service.append(j['id'],entry)
    result=await service.organize(j['id'])
    assert len(result['entries'])==1 and len(result['revisions'])==1
    assert result['connections'][0]['id']==person.id
    assert service.family_context('books')==[]
    service.patch(j['id'],JournalPatch(visibility='family'))
    assert service.family_context('books')[0]['revision_id']==result['current']['id']
    service.append(j['id'],EntryRequest(text='Correction: it was toys, not books.',request_id='second'))
    assert service.family_context('books')==[]  # stale organized account suppressed
    model.return_value={**note(),'observations':'Sample Person helped sort toys.'}
    result=await service.organize(j['id'])
    assert len(result['revisions'])==2
    assert result['entries'][0]['text']==entry.text
    assert result['revisions'][0]['observations']=='Sample Person helped sort books.'
    assert len(result['current']['source_entry_ids'])==2
    assert service.family_context('books')==[]
    assert service.family_context('toys')
    await service.organize(j['id']);assert model.await_count==2
    service.patch(j['id'],JournalPatch(visibility='parents'))
    assert service.family_context('toys')==[]
    service.patch(j['id'],JournalPatch(visibility='family',archived=True))
    assert service.family_context('toys')==[]
    links=service.db.execute('SELECT relationship_type FROM links').fetchall()
    assert {'has_entry','has_revision','derived_from','involves'} <= {r[0] for r in links}


@pytest.mark.asyncio
async def test_failure_retry_and_invalid_link_are_atomic(service,monkeypatch):
    model=AsyncMock(side_effect=RuntimeError('unavailable'))
    monkeypatch.setattr('agent_server.journal.generate_chat_json',model)
    j=service.create(NewJournal());service.append(j['id'],EntryRequest(text='An observation.',request_id='one'))
    with pytest.raises(RuntimeError):await service.organize(j['id'])
    assert service.get(j['id'])['pending']
    assert len(service.get(j['id'])['entries'])==1
    model.side_effect=None;model.return_value={**note(),'person_ids':['invented']}
    with pytest.raises(ValueError):await service.organize(j['id'])
    assert service.get(j['id'])['revisions']==[]
    model.return_value=note();await service.organize(j['id'])
    assert len(service.get(j['id'])['entries'])==1
    assert len(service.get(j['id'])['revisions'])==1


def test_idempotency_collision_archive_and_blank(service):
    j=service.create(NewJournal());service.append(j['id'],EntryRequest(text='First',request_id='one'))
    with pytest.raises(ValueError):service.append(j['id'],EntryRequest(text='Different',request_id='one'))
    with pytest.raises(ValueError):service.append(j['id'],EntryRequest(text=' ',request_id='two'))
    service.patch(j['id'],JournalPatch(archived=True))
    with pytest.raises(ValueError):service.append(j['id'],EntryRequest(text='More',request_id='three'))


@pytest.mark.asyncio
async def test_api_saved_failure_and_conversation_privacy(monkeypatch):
    from httpx import AsyncClient, ASGITransport
    from agent_server.app import app, knowledge_store, conversation_db
    knowledge_store.connect();await conversation_db.connect()
    model=AsyncMock(return_value=note())
    monkeypatch.setattr('agent_server.journal.generate_chat_json',model)
    prompts=[]
    async def reply(prompt,history,text):
        prompts.append(prompt)
        return {'reply_text':'Thanks for sharing.', 'topics':[], 'facts':[]}
    monkeypatch.setattr('agent_server.modes.chat.generate_chat_json',reply)
    async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test') as c:
        j=(await c.post('/api/journals',json={})).json();id=j['id']
        assert (await c.post(f'/api/journals/{id}/entries',json={'text':'Sample Person helped sort books.','request_id':'one'})).status_code==200
        model.side_effect=RuntimeError('fail')
        assert (await c.post(f'/api/journals/{id}/organize')).status_code==503
        saved=(await c.get(f'/api/journals/{id}')).json()
        assert len(saved['entries'])==1 and saved['pending']
        model.side_effect=None
        assert (await c.post(f'/api/journals/{id}/organize')).status_code==200
        for visibility,expected in [('parents',False),('family',True),('parents',False)]:
            await c.patch(f'/api/journals/{id}',json={'visibility':visibility})
            await c.post('/conversation',json={'text':'Can you encourage helping with books?','conversation_id':visibility,'source':'parent'})
            assert ('Sample Person helped sort books.' in prompts[-1]) is expected
        graph=(await c.get('/api/graph')).json()
        assert any(n['type']=='journal_entry' for n in graph['nodes'])
        assert any(l['type']=='derived_from' for l in graph['links'])
        assert (await c.get('/web/journal.js')).status_code==200
        assert (await c.get('/api/journals/missing')).status_code==404
    await conversation_db.close()


@pytest.mark.asyncio
async def test_concurrent_organization_and_new_entry(service,monkeypatch):
    import asyncio
    started=asyncio.Event();finish=asyncio.Event()
    async def delayed(*args):
        started.set();await finish.wait();return note()
    monkeypatch.setattr('agent_server.journal.generate_chat_json',delayed)
    j=service.create(NewJournal());service.append(j['id'],EntryRequest(text='First',request_id='one'))
    task=asyncio.create_task(service.organize(j['id']));await started.wait()
    service.append(j['id'],EntryRequest(text='Later correction',request_id='two'))
    finish.set();result=await task
    assert result['pending'] and len(result['current']['source_entry_ids'])==1
    await asyncio.gather(service.organize(j['id']),service.organize(j['id']))
    assert len(service.get(j['id'])['revisions'])==2
