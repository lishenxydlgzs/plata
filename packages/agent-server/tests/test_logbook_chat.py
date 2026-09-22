import json
from unittest.mock import AsyncMock
import pytest
from agent_server.journal import JournalService, JournalPatch
from agent_server.knowledge import KnowledgeStore
from agent_server.logbook_chat import LogbookChat, ChatRequest

@pytest.fixture
def chat():
    k=KnowledgeStore(); k.connect()
    yield LogbookChat(JournalService(k))
    k.store._db.close()

def content(source, text='A child helped sort books.'):
    return dict(title='Helping with books', observations=text, author_reflections='I noticed initiative.',
                suggestions='', parking_lot='', original_quotes=[{'message_id':source,'text':text}],
                person_ids=[], topic_ids=[], guidance_ids=[])

def output(name=None,args=None):
    return {'reply':'Recorded.', 'tool_calls':[{'name':name,'arguments':args}] if name else []}

@pytest.mark.asyncio
async def test_chat_crud_quotes_and_idempotency(chat,monkeypatch):
    session=chat.create_session(); sid=session['id']
    calls=[]
    async def generate(prompt,history,text):
        payload=json.loads(text); calls.append(payload)
        source=payload['messages'][-1]['id']
        if not payload['notes']:
            return output('create_note',{'content':content(source)})
        id=payload['notes'][0]['id']
        msg=payload['messages'][-1]['text']
        if msg.startswith('Correction'):
            return output('update_note',{'note_id':id,'content':content(source,'A child helped sort toys.')})
        return output('restore_note' if msg=='Restore it.' else 'delete_note',{'note_id':id})
    monkeypatch.setattr('agent_server.logbook_chat.generate_chat_json',generate)
    req=ChatRequest(text='A child helped sort books.',request_id='one')
    result=await chat.send(sid,req)
    assert len(result['messages'])==2 and not result['pending']
    again=await chat.send(sid,req); assert len(again['messages'])==2 and len(calls)==1
    note=chat.journals.get(chat.journals.list()[0]['id']); id=note['id']
    assert note['current']['original_quotes'][0]['text']==req.text
    assert len(note['revisions'])==1 and note['visibility']=='parents'
    chat.journals.patch(id,JournalPatch(visibility='family'))
    assert chat.journals.family_context('books')
    assert chat.journals.family_context('initiatives')
    await chat.send(sid,ChatRequest(text='Correction: A child helped sort toys.',request_id='two',selected_note_id=id))
    note=chat.journals.get(id)
    assert len(note['revisions'])==2 and note['revisions'][0]['original_quotes'][0]['text']==req.text
    assert note['current']['observations']=='A child helped sort toys.'
    await chat.send(sid,ChatRequest(text='Delete it.',request_id='three',selected_note_id=id))
    assert chat.journals.family_context('toys')==[]
    assert chat.session(sid)['messages'][0]['text']==req.text
    await chat.send(sid,ChatRequest(text='Restore it.',request_id='four',selected_note_id=id))
    assert chat.journals.family_context('toys')
    links=chat.db.execute("SELECT * FROM links WHERE relationship_type='derived_from'").fetchall()
    assert len(links)==2

@pytest.mark.asyncio
async def test_invalid_quote_batch_atomic_and_retry(chat,monkeypatch):
    sid=chat.create_session()['id']
    async def invalid(prompt,history,text):
        source=json.loads(text)['messages'][-1]['id']
        return {'reply':'Saved.', 'tool_calls':[{'name':'create_note','arguments':{'content':content(source)}},
            {'name':'create_note','arguments':{'content':content(source,'Invented quotation.')}}]}
    monkeypatch.setattr('agent_server.logbook_chat.generate_chat_json',invalid)
    request=ChatRequest(text='A child helped sort books.',request_id='same')
    with pytest.raises(ValueError): await chat.send(sid,request)
    assert chat.journals.list()==[] and chat.session(sid)['pending']['text']==request.text
    with pytest.raises(ValueError): await chat.send(sid,ChatRequest(text='Different',request_id='same'))
    with pytest.raises(ValueError): await chat.send(sid,ChatRequest(text='Next',request_id='next'))
    async def valid(prompt,history,text):
        return output('create_note',{'content':content(json.loads(text)['messages'][-1]['id'])})
    monkeypatch.setattr('agent_server.logbook_chat.generate_chat_json',valid)
    await chat.send(sid,request)
    assert len(chat.journals.list())==1 and len(chat.session(sid)['messages'])==2

@pytest.mark.asyncio
async def test_read_tool_question_and_stale_edit(chat,monkeypatch):
    sid=chat.create_session()['id']
    async def initial(prompt,history,text):
        return output('create_note',{'content':content(json.loads(text)['messages'][-1]['id'])})
    monkeypatch.setattr('agent_server.logbook_chat.generate_chat_json',initial)
    await chat.send(sid,ChatRequest(text='A child helped sort books.',request_id='1'))
    note=chat.journals.list()[0]; nid=note['id']; read_calls=[]
    async def read(prompt,history,text):
        payload=json.loads(text); read_calls.append(payload)
        if not payload['tool_results']:return output('read_note',{'note_id':nid})
        assert payload['tool_results'][0]['result']['sources'][0]['text']=='A child helped sort books.'
        return output()
    monkeypatch.setattr('agent_server.logbook_chat.generate_chat_json',read)
    # A fresh conversation can retrieve notes and answer without a write.
    sid=chat.create_session()['id']
    await chat.send(sid,ChatRequest(text='What did I observe?',request_id='2'))
    assert len(read_calls)==2 and len(chat.journals.get(nid)['revisions'])==1
    async def stale(prompt,history,text):
        chat.journals.patch(nid,JournalPatch(title='Changed elsewhere'))
        return output('delete_note',{'note_id':nid})
    monkeypatch.setattr('agent_server.logbook_chat.generate_chat_json',stale)
    with pytest.raises(ValueError):await chat.send(sid,ChatRequest(text='Delete it.',request_id='3',selected_note_id=nid))
    assert not chat.journals.get(nid)['archived']

@pytest.mark.asyncio
async def test_api_failure_reload_retry(chat,monkeypatch):
    from httpx import AsyncClient, ASGITransport
    from agent_server.app import app
    monkeypatch.setattr('agent_server.app.logbook_chat',chat)
    monkeypatch.setattr('agent_server.logbook_chat.generate_chat_json',AsyncMock(side_effect=RuntimeError('offline')))
    async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test') as c:
        sid=(await c.post('/api/logbook/sessions')).json()['id']
        url=f'/api/logbook/sessions/{sid}'
        req={'text':'A thought','request_id':'one'}
        assert (await c.post(url+'/messages',json=req)).status_code==503
        assert (await c.get(url)).json()['pending']['text']=='A thought'
        monkeypatch.setattr('agent_server.logbook_chat.generate_chat_json',AsyncMock(return_value=output()))
        assert (await c.post(url+'/messages',json=req)).status_code==200
        assert (await c.get(url)).json()['pending'] is None
        assert len(chat.journals.list())==0

@pytest.mark.asyncio
async def test_conversation_can_gather_observation_then_reflection(chat, monkeypatch):
    sid = chat.create_session()['id']
    async def respond(prompt, history, text):
        payload = json.loads(text)
        users = [m for m in payload['messages'] if m['role'] == 'user']
        if len(users) == 1:
            return {'reply': 'What stood out to you about that?', 'tool_calls': []}
        if len(users) == 2:
            c = content(users[0]['id'])
            c['author_reflections'] = users[1]['text']
            c['original_quotes'].append({'message_id': users[1]['id'], 'text': users[1]['text']})
            return {'reply': 'It sounds like giving them room mattered to you.',
                    'tool_calls': [{'name': 'create_note', 'arguments': {'content': c}}]}
        return {'reply': 'We can keep exploring that.', 'tool_calls': []}
    monkeypatch.setattr('agent_server.logbook_chat.generate_chat_json', respond)
    first = await chat.send(sid, ChatRequest(text='A child helped sort books.', request_id='observation'))
    assert not first['pending'] and chat.journals.list() == []
    second = await chat.send(sid, ChatRequest(text='I realized I often step in too quickly.', request_id='reflection'))
    n = chat.journals.get(chat.journals.list()[0]['id'])
    assert [q['text'] for q in n['current']['original_quotes']] == [
        'A child helped sort books.', 'I realized I often step in too quickly.']
    assert len(n['current']['source_entry_ids']) == 2
    await chat.send(sid, ChatRequest(text='Let me think a little more.', request_id='thinking'))
    assert len(chat.journals.get(n['id'])['revisions']) == 1
    assert len(chat.session(sid)['messages']) == 6

@pytest.mark.asyncio
async def test_note_generation_uses_its_own_deadline_and_output_budget(monkeypatch):
    from agent_server import logbook_chat
    model = AsyncMock(return_value=output())
    monkeypatch.setattr(logbook_chat, '_generate_chat_json', model)
    await logbook_chat.generate_chat_json('prompt', [], 'message')
    model.assert_awaited_once_with('prompt', [], 'message', timeout_seconds=45.0, max_output_tokens=4096)
