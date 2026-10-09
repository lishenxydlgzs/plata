"""Telegram transport and authorization contracts; no live network or model calls."""

import asyncio
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI

from agent_server.telegram import (
    MEMORY_NOTICE, TelegramAPI, TelegramError, TelegramService, chunks, telegram_router,
)
from agent_server.models import ConversationResponse, ConversationMode

TOKEN = '12345:' + 'x' * 32


class FakeAPI:
    def __init__(self):
        self.sent = []
        self.calls = []
        self.failure = None
        self.bot_id = 99
        self.webhook = ''

    async def call(self, token, method, **payload):
        self.calls.append((method, payload))
        if method == 'getMe':
            return {'id': self.bot_id, 'username': 'sample_plata_bot'}
        if method == 'getWebhookInfo':
            return {'url': self.webhook}
        if method == 'sendMessage':
            if self.failure:
                raise self.failure
            self.sent.append(payload)
        return True

    async def close(self):
        pass


@pytest.fixture
async def service(tmp_path):
    router = SimpleNamespace(route=AsyncMock(return_value=ConversationResponse(
        reply_text='Let’s draw a rainy-day map.', mode=ConversationMode.CHAT)))
    knowledge = SimpleNamespace(get_people=lambda: [
        {'id': 'parent-a', 'name': 'Sample Parent'}, {'id': 'parent-b', 'name': 'Other Parent'}])
    service = TelegramService(router, knowledge, tmp_path / 'telegram' / 'state.db', FakeAPI())
    service.open()
    service.launch = lambda: None
    await service.configure(TOKEN)
    yield service
    await service.stop()


def update(id, text='Hello', user=101, chat_type='private'):
    return {'update_id': id, 'message': {'text': text, 'chat': {'id': user, 'type': chat_type},
            'from': {'id': user, 'first_name': 'Sample User', 'language_code': 'en'}}}


async def receive(service, message):
    service.ingest([message])
    await service.process()


async def pair(service, person='parent-a', user=101, update_id=1):
    invitation = service.invite(person)
    await receive(service, update(update_id, '/start ' + invitation['url'].split('=')[1], user))
    service.approve(invitation['id'])
    return invitation


async def test_setup_validates_token_and_never_returns_it(service):
    status = service.status()
    assert status['configured'] and status['username'] == 'sample_plata_bot'
    assert TOKEN not in json.dumps(status)
    assert service.path.stat().st_mode & 0o777 == 0o600
    assert service.path.parent.stat().st_mode & 0o777 == 0o700
    assert {'getMe', 'getWebhookInfo', 'setMyDescription', 'setMyCommands'} <= {c[0] for c in service.api.calls}
    with pytest.raises(ValueError, match='token did not work'):
        await service.configure('bad')
    service.api.webhook = 'https://example.invalid/webhook'
    with pytest.raises(ValueError, match='webhook'):
        await service.configure(TOKEN)
    assert service.status()['configured']


async def test_invitation_requires_confirmation_and_is_single_use(service):
    invite = service.invite('parent-a')
    token = invite['url'].split('=')[1]
    row = service.db.execute('SELECT * FROM invites').fetchone()
    assert row['digest'] == hashlib.sha256(token.encode()).hexdigest()
    assert token not in json.dumps(service.status())
    await receive(service, update(1, '/start ' + token))
    assert service.status()['invitations'][0]['state'] == 'pending'
    await receive(service, update(2))
    service.router.route.assert_not_awaited()
    await receive(service, update(3, '/start ' + token, user=202))
    assert service.status()['invitations'][0]['user_id'] == 101
    service.approve(invite['id'])
    await receive(service, update(4))
    request = service.router.route.call_args.args[0]
    assert request.person_id == 'parent-a' and request.source == 'telegram'
    assert service.status()['accounts'][0]['first_reply'] is None
    await service.flush()
    assert service.status()['accounts'][0]['first_reply'] is not None
    assert any(MEMORY_NOTICE in r['text'] for r in service.api.sent)


async def test_expiry_and_replacement(service):
    old = service.invite('parent-a')
    new = service.invite('parent-a')
    await receive(service, update(1, '/start ' + old['url'].split('=')[1]))
    assert service.status()['invitations'][0]['state'] == 'waiting'
    await receive(service, update(2, '/start ' + new['url'].split('=')[1]))
    service.db.execute('UPDATE invites SET expires=0')
    with pytest.raises(ValueError, match='expired'):
        service.approve(new['id'])
    assert service.status()['invitations'][0]['state'] == 'expired'
    assert not service.status()['accounts']


async def test_group_and_unauthorized_messages_never_reach_agent(service):
    await receive(service, update(1))
    invitation = service.invite('parent-a')
    await receive(service, update(2, '/start ' + invitation['url'].split('=')[1], chat_type='group'))
    service.router.route.assert_not_awaited()
    assert service.status()['invitations'][0]['state'] == 'waiting'
    assert service.db.execute('SELECT COUNT(*) FROM outbox').fetchone()[0] == 1


async def test_separate_people_have_separate_persistent_history_and_new_chat(service):
    await pair(service)
    await pair(service, 'parent-b', 202, 2)
    await receive(service, update(3))
    await receive(service, update(4, user=202))
    requests = [c.args[0] for c in service.router.route.call_args_list]
    assert requests[0].conversation_id != requests[1].conversation_id
    first = requests[0].conversation_id
    await receive(service, update(5, '/new'))
    await receive(service, update(6))
    assert service.router.route.call_args.args[0].conversation_id != first
    assert len(service.status()['accounts']) == 2


async def test_duplicate_updates_and_send_retries_do_not_repeat_agent(service):
    await pair(service)
    service.ingest([update(2), update(2)])
    await service.process()
    await receive(service, update(2))
    service.router.route.assert_awaited_once()
    service.api.failure = TelegramError('Temporary failure')
    with pytest.raises(TelegramError):
        await service.flush()
    assert service.status()['accounts'][0]['first_reply'] is None
    service.api.failure = None
    await service.flush()
    assert service.status()['accounts'][0]['first_reply']
    assert service.db.execute('SELECT COUNT(*) FROM outbox').fetchone()[0] == 0
    service.router.route.assert_awaited_once()


async def test_blocked_bot_records_delivery_problem_without_stalling_other_accounts(service):
    await pair(service)
    service.api.failure = TelegramError('Unblock bot', permanent=True)
    await service.flush()
    assert service.status()['accounts'][0]['delivery_error'] == 'Unblock bot'
    assert service.db.execute('SELECT COUNT(*) FROM outbox').fetchone()[0] == 0


async def test_disconnect_revokes_pending_output_and_future_access(service):
    await pair(service)
    await receive(service, update(2))
    service.revoke(101)
    assert service.db.execute('SELECT COUNT(*) FROM outbox').fetchone()[0] == 0
    await receive(service, update(3))
    service.router.route.assert_awaited_once()
    assert not service.status()['accounts']


async def test_self_disconnect_cancels_pending_invitation(service):
    invite = service.invite('parent-a')
    await receive(service, update(1, '/start ' + invite['url'].split('=')[1]))
    await receive(service, update(2, '/disconnect'))
    with pytest.raises(ValueError):
        service.approve(invite['id'])


async def test_bot_rotation_preserves_same_bot_but_clears_different_bot(service):
    await pair(service)
    await service.configure('12345:' + 'y' * 32)
    assert len(service.status()['accounts']) == 1
    service.api.bot_id = 100
    await service.configure('67890:' + 'z' * 32)
    assert service.status()['accounts'] == []
    assert service.config()['offset'] == 0
    assert service.db.execute('SELECT COUNT(*) FROM outbox').fetchone()[0] == 0
    await service.disconnect_bot()
    assert not service.status()['configured']


async def test_restart_preserves_queued_updates_and_does_not_reexecute_ambiguous_turn(service):
    await pair(service)
    await service.flush()
    service.ingest([update(2), update(3, 'Next message')])
    service.db.execute("UPDATE inbox SET state='processing' WHERE update_id=2")
    service.db.commit()
    conversation_id = service.db.execute('SELECT conversation_id FROM accounts').fetchone()[0]
    await service.stop()
    await service.start()
    assert service.config()['offset'] == 4
    assert service.db.execute('SELECT conversation_id FROM accounts').fetchone()[0] == conversation_id
    await service.process()
    service.router.route.assert_awaited_once()
    assert service.router.route.call_args.args[0].text == 'Next message'
    await service.flush()
    assert any('restarted' in r['text'] for r in service.api.sent)


async def test_multiple_workers_cannot_open_same_state(service):
    other = TelegramService(service.router, service.knowledge, service.path, FakeAPI())
    with pytest.raises(RuntimeError, match='one worker'):
        other.open()


async def test_media_attachment_does_not_call_agent(service):
    await pair(service)
    message = update(2)
    del message['message']['text']
    message['message']['voice'] = {'file_id': 'sample'}
    await receive(service, message)
    service.router.route.assert_not_awaited()


def test_reply_chunking_preserves_unicode():
    text = 'a' * 3999 + '😀' * 3000
    parts = list(chunks(text))
    assert ''.join(parts) == text
    assert all(len(part.encode('utf-16-le')) // 2 <= 4000 for part in parts)


@pytest.fixture
async def client(service):
    app = FastAPI()
    app.include_router(telegram_router(service))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://test') as client:
        yield client


async def test_workspace_csrf_and_secret_boundaries(client):
    missing_header = await client.get('/api/telegram')
    assert missing_header.status_code == 403
    assert 'X-Plata-Workspace: 1' in missing_header.json()['detail']
    headers = {'X-Plata-Workspace': '1'}
    assert (await client.get('/api/telegram', headers={**headers, 'Origin': 'https://evil.invalid'})).status_code == 403
    status = await client.get('/api/telegram', headers=headers)
    assert status.status_code == 200 and TOKEN not in status.text
    assert status.headers['cache-control'] == 'no-store'
    response = await client.post('/api/telegram/invitations', headers=headers,
                                 json={'person_id': 'parent-a', 'shared_memory_acknowledged': False})
    assert response.status_code == 422
    response = await client.post('/api/telegram/invitations', headers=headers,
                                 json={'person_id': 'missing', 'shared_memory_acknowledged': True})
    assert response.status_code == 400


async def test_qr_generated_locally_and_cancellation(client, service):
    headers = {'X-Plata-Workspace': '1'}
    response = await client.post('/api/telegram/invitations', headers=headers,
                                json={'person_id': 'parent-a', 'shared_memory_acknowledged': True})
    invitation = response.json()
    qr = await client.post('/api/telegram/qr', headers=headers, json={'url': invitation['url']})
    assert qr.status_code == 200 and '<svg' in qr.text
    assert 'image/svg+xml' in qr.headers['content-type']
    assert (await client.post('/api/telegram/qr', headers=headers, json={'url': 'https://evil.invalid'})).status_code == 422
    assert (await client.delete('/api/telegram/invitations/' + invitation['id'], headers=headers)).status_code == 200
    assert service.status()['invitations'] == []


@pytest.mark.parametrize('code', [400, 401, 403, 409, 429, 500])
async def test_transport_never_exposes_token_in_errors(code):
    api = TelegramAPI()
    await api.client.aclose()
    api.client = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(code,
        json={'ok': False, 'error_code': code, 'description': TOKEN, 'parameters': {'retry_after': 17}})))
    with pytest.raises(TelegramError) as error:
        await api.call(TOKEN, 'getUpdates')
    assert TOKEN not in str(error.value)
    assert error.value.retry_after == 17
    await api.close()


async def test_transport_network_errors_are_redacted():
    def fail(request):
        raise httpx.ConnectError(TOKEN, request=request)
    api = TelegramAPI()
    await api.client.aclose()
    api.client = httpx.AsyncClient(transport=httpx.MockTransport(fail))
    with pytest.raises(TelegramError) as error:
        await api.call(TOKEN, 'getUpdates')
    assert TOKEN not in str(error.value)
    await api.close()


async def test_poll_loop_ingests_and_sends_then_stops_cleanly(service):
    await pair(service)
    await service.flush()
    original_call = service.api.call
    sent = asyncio.Event()
    async def call(token, method, **payload):
        if method == 'getUpdates':
            if payload['offset'] == 2:
                return [update(2)]
            await asyncio.Event().wait()
        result = await original_call(token, method, **payload)
        if method == 'sendMessage':
            sent.set()
        return result
    service.api.call = call
    service.task = asyncio.create_task(service.run())
    await asyncio.wait_for(sent.wait(), 2)
    await service.stop_worker()
    assert service.task is None
    service.router.route.assert_awaited_once()


async def test_disconnect_discards_queued_work_even_in_same_batch(service):
    await pair(service)
    service.ingest([update(2, '/disconnect'), update(3, 'Queued message')])
    await service.process()
    service.router.route.assert_not_awaited()
    assert service.db.execute("SELECT COUNT(*) FROM inbox WHERE state='queued'").fetchone()[0] == 0
    # A redelivery below the durable offset must not resurrect a discarded turn.
    service.ingest([update(3, 'Queued message')])
    assert service.db.execute('SELECT COUNT(*) FROM inbox WHERE update_id=3').fetchone()[0] == 0


async def test_pending_disconnect_removes_stale_pairing_notice(service):
    invitation = service.invite('parent-a')
    await receive(service, update(1, '/start ' + invitation['url'].split('=')[1]))
    await receive(service, update(2, '/disconnect'))
    await service.flush()
    assert len(service.api.sent) == 1
    assert 'removed' in service.api.sent[0]['text']


async def test_first_conversation_requires_delivery_of_final_reply_chunk(service):
    await pair(service)
    await service.flush()
    service.queue(101, 'a' * 9000, first_reply=True)
    markers = [row[0] for row in service.db.execute('SELECT first_reply FROM outbox ORDER BY id')]
    assert markers == [0, 0, 1]


async def test_headless_api_onboarding_and_cleanup_for_two_people(service, monkeypatch):
    """Exercise the documented admin flow entirely through HTTP, without a UI session.

    Only incoming Telegram messages and outbound Telegram/model calls are simulated;
    people creation, invitations, approval, status, and removal use real API handlers.
    """
    from agent_server import app as application
    from agent_server.knowledge import KnowledgeStore

    store = KnowledgeStore()
    store.connect()
    monkeypatch.setattr(application, 'knowledge_store', store)
    service.knowledge = store
    await service.disconnect_bot()
    app = FastAPI()
    app.include_router(telegram_router(service))
    app.add_api_route('/api/people', application.list_people, methods=['GET'])
    app.add_api_route('/api/people', application.create_person, methods=['POST'])
    try:
        # A non-browser coding agent supplies the workspace header, no Origin/cookies.
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://test',
                                     headers={'X-Plata-Workspace': '1'}) as client:
            assert not (await client.get('/api/telegram')).json()['configured']
            response = await client.put('/api/telegram/bot', json={'token': TOKEN})
            assert response.status_code == 200
            assert response.json()['configured'] and TOKEN not in response.text
            assert (await client.get('/api/people')).json() == []

            people = []
            for index, name in enumerate(('Sample Parent', 'Other Parent')):
                user_id, update_id = 101 + index, index * 10 + 1
                response = await client.post('/api/people', json={'name': name, 'aliases': []})
                assert response.status_code == 200
                person = response.json()
                people.append(person)
                assert person['id'] in {p['id'] for p in (await client.get('/api/people')).json()}

                response = await client.post('/api/telegram/invitations', json={
                    'person_id': person['id'], 'shared_memory_acknowledged': True})
                assert response.status_code == 200
                invitation = response.json()
                assert set(invitation) == {'id', 'url', 'expires'}
                status = (await client.get('/api/telegram')).json()
                assert status['invitations'][0]['state'] == 'waiting'
                assert (await client.post(f"/api/telegram/invitations/{invitation['id']}/approve")).status_code == 400

                # User-owned Telegram step: open the invitation and press Start.
                await receive(service, update(update_id, '/start ' + invitation['url'].split('=')[1], user_id))
                status = (await client.get('/api/telegram')).json()
                pending = next(i for i in status['invitations'] if i['id'] == invitation['id'])
                assert pending['state'] == 'pending' and pending['user_id'] == user_id
                await receive(service, update(update_id + 1, 'Not yet approved', user_id))
                assert service.router.route.await_count == index

                response = await client.post(f"/api/telegram/invitations/{invitation['id']}/approve")
                assert response.status_code == 200 and response.json() == {'ok': True}
                status = (await client.get('/api/telegram')).json()
                assert status['invitations'] == []
                account = next(a for a in status['accounts'] if a['user_id'] == user_id)
                assert account['person_id'] == person['id'] and account['first_reply'] is None
                assert (await client.post(f"/api/telegram/invitations/{invitation['id']}/approve")).status_code == 400

                await receive(service, update(update_id + 2, 'Help me think of a rainy-day activity.', user_id))
                await service.flush()
                status = (await client.get('/api/telegram')).json()
                account = next(a for a in status['accounts'] if a['user_id'] == user_id)
                assert account['first_reply'] and account['delivery_error'] is None
                assert service.router.route.call_args.args[0].person_id == person['id']

            requests = [call.args[0] for call in service.router.route.call_args_list]
            assert len(requests) == 2 and requests[0].conversation_id != requests[1].conversation_id
            assert (await client.delete('/api/telegram/accounts/101')).status_code == 200
            await receive(service, update(21, 'Access has been revoked', 101))
            assert service.router.route.await_count == 2
            assert len((await client.get('/api/telegram')).json()['accounts']) == 1

            response = await client.post('/api/telegram/invitations', json={
                'person_id': people[0]['id'], 'shared_memory_acknowledged': True})
            invitation = response.json()
            assert (await client.delete(f"/api/telegram/invitations/{invitation['id']}")).status_code == 200
            await receive(service, update(22, '/start ' + invitation['url'].split('=')[1], 101))
            assert (await client.get('/api/telegram')).json()['invitations'] == []
            assert (await client.delete('/api/telegram/bot')).status_code == 200
            status = (await client.get('/api/telegram')).json()
            assert not status['configured'] and status['accounts'] == []
    finally:
        store.store._db.close()
