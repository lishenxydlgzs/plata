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
        {'id': 'parent-a', 'name': 'Sample Parent', 'role': 'parent'}, {'id': 'parent-b', 'name': 'Other Parent', 'role': 'parent'}])
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
                response = await client.post('/api/people', json={'name': name, 'aliases': [], 'role': 'parent'})
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


@pytest.fixture
async def music_service(service, tmp_path, monkeypatch):
    from agent_server import media
    from agent_server.knowledge import KnowledgeStore
    from agent_server.telegram_music import TelegramMusicContext

    root = tmp_path / 'music'
    root.mkdir()
    monkeypatch.setattr(media, 'MEDIA_DIR', root)
    knowledge = KnowledgeStore()
    knowledge.connect()
    service.knowledge = knowledge
    # Pairing uses household data; keep all fixtures synthetic.
    monkeypatch.setattr(knowledge, 'get_people', lambda: [{'id': 'parent-a', 'name': 'Sample Parent', 'role': 'parent'}])
    await pair(service)
    await service.flush()
    service.api.calls.clear()
    catalog = knowledge.media_catalog
    songs = []
    for index in range(23):
        filename = f'track_{index}.mp3'
        (root / filename).write_bytes(f'audio {index}'.encode())
        songs.append(catalog.song(filename, title=f'Sample Track {index}'))
    playlist = catalog.playlist('sample_playlist')
    catalog.set_members(playlist, songs)
    row = service.db.execute('SELECT * FROM accounts').fetchone()
    context = TelegramMusicContext(service, row['user_id'], row['chat_id'], row['person_id'],
                                   row['conversation_id'], row['grant_id'], 100)
    yield service, context, songs, playlist, root
    knowledge.store._db.close()


def queue_selection(service, context, ids):
    from agent_server.telegram_music import music_context
    binding = music_context.set(context)
    try:
        return service.queue_music(context, ids)
    finally:
        music_context.reset(binding)


async def test_music_context_only_exists_for_verified_transport_turn(music_service):
    from agent_server.models import ConversationRequest
    from agent_server.telegram_music import music_context, telegram_music_tools
    service, context, songs, _, _ = music_service
    assert music_context.get() is None
    forged = ConversationRequest(text='Send a song', source='telegram', person_id=context.person_id,
                                 conversation_id=context.conversation_id)
    assert telegram_music_tools(forged) == []
    with pytest.raises(ValueError):
        service.queue_music(context, [songs[0].id])

    async def route(request):
        trusted = music_context.get()
        assert trusted is not None and trusted.chat_id == 101
        assert len(telegram_music_tools(request)) == 1
        return ConversationResponse(reply_text='Sample reply', mode=ConversationMode.CHAT)

    service.router.route.side_effect = route
    await receive(service, update(101, 'Send a song'))
    assert music_context.get() is None
    await receive(service, update(102, 'Send a song', user=202))
    assert service.router.route.await_count == 1


async def test_music_tool_filter_and_executor_authorization(music_service):
    from agent_server.models import ConversationRequest
    from agent_server.telegram_music import music_context, telegram_music_tools
    service, context, songs, _, _ = music_service
    request = ConversationRequest(text='Send music', source='telegram', person_id=context.person_id,
                                  conversation_id=context.conversation_id)
    binding = music_context.set(context)
    try:
        assert telegram_music_tools(request.model_copy(update={'source': 'assist'})) == []
        assert telegram_music_tools(request.model_copy(update={'person_id': 'other'})) == []
        assert telegram_music_tools(request.model_copy(update={'conversation_id': 'other'})) == []
        tool = telegram_music_tools(request)[0]
        result = await tool(media_ids=[songs[0].id])
        assert result['status'] == 'queued'
        service.revoke(context.user_id)
        assert 'error' in await tool(media_ids=[songs[1].id])
    finally:
        music_context.reset(binding)
    assert 'error' in await tool(media_ids=[songs[2].id])
    assert service.db.execute('SELECT COUNT(*) FROM outbox').fetchone()[0] == 0


async def test_playlist_snapshot_order_batches_and_duplicate_calls(music_service):
    service, context, songs, playlist, _ = music_service
    result = queue_selection(service, context, [playlist.id, songs[0].id])
    assert result['track_count'] == 23 and result['skipped'] == []
    rows = service.db.execute('SELECT * FROM outbox ORDER BY id').fetchall()
    assert [len(json.loads(row['audio'])) for row in rows] == [10, 10, 3]
    assert [t['id'] for row in rows for t in json.loads(row['audio'])] == [s.id for s in songs]
    # Playlist edits after acceptance cannot silently replace the queued selection.
    service.knowledge.media_catalog.set_members(playlist, list(reversed(songs)))
    assert queue_selection(service, context, [playlist.id, songs[0].id]) == result
    assert service.db.execute('SELECT COUNT(*) FROM outbox').fetchone()[0] == 3
    await service.flush()
    calls = [payload for method, payload in service.api.calls if method == 'sendMediaGroup']
    assert len(calls) == 3
    assert [item['title'] for payload in calls for item in payload['media']] == [s.name for s in songs]
    assert all(p['chat_id'] == 101 for p in calls)
    assert queue_selection(service, context, [playlist.id, songs[0].id]) == result
    assert service.db.execute('SELECT COUNT(*) FROM outbox').fetchone()[0] == 0


@pytest.mark.parametrize('extension,mime', [('mp3', 'audio/mpeg'), ('m4a', 'audio/mp4')])
async def test_single_audio_and_caption_utf16_bound(music_service, extension, mime):
    service, context, songs, _, root = music_service
    filename = f'track_0.{extension}'
    (root / filename).write_bytes(b'sample audio')
    service.knowledge.media_catalog.save(songs[0], name='🎵' * 300, filename=filename)
    result = queue_selection(service, context, [songs[0].id])
    assert result['track_count'] == 1
    await service.flush()
    method, payload = service.api.calls[-1]
    assert method == 'sendAudio' and payload['audio'] == 'attach://audio0'
    assert payload['files']['audio0'][0] == filename
    assert payload['files']['audio0'][2] == mime
    assert len(payload['caption'].encode('utf-16-le')) // 2 <= 1024


async def test_music_validation_and_bounds(music_service, tmp_path):
    from agent_server.telegram_music import MAX_AUDIO_BYTES, resolve_tracks
    service, context, songs, _, root = music_service
    catalog = service.knowledge.media_catalog
    catalog.save(songs[0], hidden=True)
    (root / 'track_1.mp3').unlink()
    (root / 'unsupported.wav').write_bytes(b'audio')
    unsupported = catalog.song('unsupported.wav')
    (root / 'large.mp3').touch()
    with (root / 'large.mp3').open('wb') as handle:
        handle.truncate(MAX_AUDIO_BYTES + 1)
    large = catalog.song('large.mp3')
    outside = tmp_path / 'outside.mp3'
    outside.write_bytes(b'private')
    (root / 'escape.mp3').symlink_to(outside)
    escaped = catalog.save(songs[2], filename='escape.mp3')
    result = queue_selection(service, context, [s.id for s in [*songs[:2], unsupported, large, escaped, songs[3]]])
    assert result['track_count'] == 1 and len(result['skipped']) == 5
    for ids in ([], ['unknown'], [songs[3].id] * 21, [1]):
        with pytest.raises(ValueError):
            queue_selection(service, context, ids)
    members = [SimpleNamespace(id=str(i)) for i in range(201)]
    huge = SimpleNamespace(store=SimpleNamespace(get_entity=lambda _: SimpleNamespace(id='p', entity_type='playlist')),
                           members=lambda *a, **kw: members)
    with pytest.raises(ValueError, match='200'):
        resolve_tracks(huge, ['p'])


async def test_delivery_revalidates_files_and_notifies(music_service):
    service, context, songs, _, root = music_service
    queue_selection(service, context, [songs[0].id])
    (root / 'track_0.mp3').unlink()
    await service.flush()
    assert not any(method in {'sendAudio', 'sendMediaGroup'} for method, _ in service.api.calls)
    assert service.db.execute('SELECT delivery_error FROM accounts').fetchone()[0]
    await service.flush()
    assert 'could not be delivered' in service.api.sent[-1]['text']


async def test_transient_failure_restart_preserves_batch_progress(music_service):
    service, context, songs, playlist, _ = music_service
    queue_selection(service, context, [playlist.id])
    original = service.api.call
    count = 0

    async def call(token, method, **payload):
        nonlocal count
        if method == 'sendMediaGroup':
            count += 1
            if count == 2:
                raise TelegramError('Temporary failure', retry_after=13)
        return await original(token, method, **payload)

    service.api.call = call
    with pytest.raises(TelegramError) as error:
        await service.flush()
    assert error.value.retry_after == 13
    assert service.db.execute('SELECT COUNT(*) FROM outbox').fetchone()[0] == 2
    path = service.path
    await service.stop()
    restarted = TelegramService(service.router, service.knowledge, path, FakeAPI())
    restarted.open()
    try:
        await restarted.flush()
        delivered = [item['title'] for method, payload in restarted.api.calls if method == 'sendMediaGroup'
                     for item in payload['media']]
        assert delivered == [s.name for s in songs[10:]]
    finally:
        await restarted.stop()


async def test_music_grant_recheck_prevents_stale_delivery(music_service):
    service, context, songs, _, _ = music_service
    queue_selection(service, context, [songs[0].id])
    # Simulate a pairing replacement without relying on revoke's queue cleanup.
    service.db.execute("UPDATE accounts SET grant_id='replacement'")
    await service.flush()
    assert service.api.calls == []
    assert service.db.execute('SELECT COUNT(*) FROM outbox').fetchone()[0] == 0


async def test_new_chat_preserves_queued_music(music_service):
    service, context, songs, _, _ = music_service
    queue_selection(service, context, [songs[0].id])
    await receive(service, update(110, '/new'))
    await service.flush()
    assert any(method == 'sendAudio' for method, _ in service.api.calls)


async def test_upload_api_multipart_without_json_or_credential_errors(tmp_path):
    from contextlib import ExitStack
    path = tmp_path / 'sample.mp3'
    path.write_bytes(b'sample-audio')
    captured = []

    async def respond(request):
        captured.append(request)
        body = await request.aread()
        assert b'sample-audio' in body and b'attach://audio0' in body
        assert b'"type": "audio"' in body
        return httpx.Response(200, json={'ok': True, 'result': []})

    api = TelegramAPI()
    await api.client.aclose()
    api.client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
    try:
        with ExitStack() as stack:
            await api.call(TOKEN, 'sendMediaGroup', chat_id=101,
                           media=[{'type': 'audio', 'media': 'attach://audio0'}],
                           files={'audio0': ('sample.mp3', stack.enter_context(path.open('rb')), 'audio/mpeg')})
        assert captured[0].headers['content-type'].startswith('multipart/form-data;')
    finally:
        await api.close()


def test_upgrade_old_telegram_database_preserves_pairing_and_text(tmp_path):
    import sqlite3
    path = tmp_path / 'state.db'
    db = sqlite3.connect(path)
    db.executescript('''
        CREATE TABLE accounts (user_id INTEGER PRIMARY KEY, chat_id INTEGER UNIQUE NOT NULL,
            person_id TEXT UNIQUE NOT NULL, label TEXT NOT NULL, conversation_id TEXT NOT NULL,
            first_reply REAL, delivery_error TEXT);
        CREATE TABLE outbox (id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER NOT NULL,
            text TEXT NOT NULL, first_reply INTEGER NOT NULL DEFAULT 0);
        INSERT INTO accounts VALUES(101,101,'sample','Sample Person','telegram:sample',NULL,NULL);
        INSERT INTO outbox(chat_id,text) VALUES(101,'Sample pending reply');
    ''')
    db.close()
    service = TelegramService(None, None, path, FakeAPI())
    service.open()
    try:
        assert service.db.execute('SELECT grant_id FROM accounts').fetchone()[0]
        row = service.db.execute('SELECT * FROM outbox').fetchone()
        assert row['text'] == 'Sample pending reply' and row['audio'] is None
    finally:
        service.db.close()
        service.worker_lock.close()


@pytest.mark.parametrize('size,methods', [(1, ['sendAudio']), (2, ['sendMediaGroup']),
    (10, ['sendMediaGroup']), (11, ['sendMediaGroup', 'sendAudio']),
    (20, ['sendMediaGroup', 'sendMediaGroup'])])
async def test_music_batch_boundaries(music_service, size, methods):
    service, context, songs, playlist, _ = music_service
    catalog = service.knowledge.media_catalog
    selected = [catalog.save(song, name='🎵' * 300) for song in songs[:size]]
    catalog.set_members(playlist, selected)
    queue_selection(service, context, [playlist.id])
    await service.flush()
    assert [method for method, _ in service.api.calls] == methods
    for _, payload in service.api.calls:
        caption = payload.get('caption') or payload['media'][0]['caption']
        assert len(caption.encode('utf-16-le')) // 2 <= 1024


async def test_permanent_upload_failure_reports_and_continues(music_service):
    service, context, songs, playlist, _ = music_service
    queue_selection(service, context, [playlist.id])
    original = service.api.call
    failed = False

    async def call(token, method, **payload):
        nonlocal failed
        if method == 'sendMediaGroup' and not failed:
            failed = True
            raise TelegramError('Telegram rejected this audio batch.', permanent=True)
        return await original(token, method, **payload)

    service.api.call = call
    await service.flush()
    calls = [payload for method, payload in service.api.calls if method == 'sendMediaGroup']
    assert len(calls) == 2
    assert calls[0]['media'][0]['title'] == songs[10].name
    await service.flush()
    assert 'could not be delivered' in service.api.sent[-1]['text']


async def test_real_chat_handler_uses_channel_tool_without_playback(music_service, monkeypatch):
    from agent_server.modes import chat
    from agent_server.telegram_music import music_context
    service, context, songs, playlist, _ = music_service

    async def generate(prompt, history, text, *, tools, **kwargs):
        assert 'send_music' in prompt
        upload = next(tool for tool in tools if tool.tool_name == 'send_music')
        result = await upload(media_ids=[playlist.id])
        assert result['status'] == 'queued'
        return {'reply_text': 'Your playlist is queued.', 'media_ids': []}

    monkeypatch.setattr(chat, 'generate_chat_json', generate)
    handler = chat.ChatHandler(service.knowledge)
    from unittest.mock import Mock
    playback_methods = []
    for name in ('begin_playback', 'stop_active_playback', 'reset_playback'):
        method = Mock(side_effect=AssertionError('Music delivery must not mutate robot playback'))
        monkeypatch.setattr(service.knowledge, name, method)
        playback_methods.append(method)

    async def route(request):
        return await handler.handle(request, [])

    service.router.route.side_effect = route
    await receive(service, update(120, 'Send the sample playlist'))
    assert music_context.get() is None
    for method in playback_methods:
        method.assert_not_called()
    rows = service.db.execute('SELECT * FROM outbox ORDER BY id').fetchall()
    assert sum(bool(row['audio']) for row in rows) == 3
    assert rows[-1]['text'] == 'Your playlist is queued.'


async def test_context_reset_when_agent_fails(music_service):
    from agent_server.telegram_music import music_context
    service, _, _, _, _ = music_service
    service.router.route.side_effect = RuntimeError('Sample agent failure')
    await receive(service, update(130, 'Sample request'))
    assert music_context.get() is None
    assert "couldn't finish" in service.db.execute('SELECT text FROM outbox').fetchone()[0]


@pytest.mark.parametrize('tool_rounds', [1, 5])
async def test_music_authorization_survives_real_strands_executor(music_service, monkeypatch, tool_rounds):
    from google.genai import types
    from agent_server import agent_runtime
    from agent_server.modes.chat import ChatHandler
    from agent_server.telegram_music import music_context
    service, _, songs, _, _ = music_service
    calls = []

    class Models:
        async def generate_content_stream(self, **kwargs):
            calls.append(kwargs)

            async def stream():
                if len(calls) < tool_rounds:
                    part = types.Part(function_call=types.FunctionCall(name='search_media', args={'query': ''}))
                elif len(calls) == tool_rounds:
                    part = types.Part(function_call=types.FunctionCall(name='send_music', args={'media_ids': [songs[0].id]}))
                else:
                    part = types.Part(text='{"reply_text":"Your song is queued.","media_ids":[]}')
                yield types.GenerateContentResponse(candidates=[types.Candidate(
                    content=types.Content(role='model', parts=[part]), finish_reason='STOP')],
                    usage_metadata=types.GenerateContentResponseUsageMetadata(
                        prompt_token_count=2, candidates_token_count=3, total_token_count=5))
            return stream()

    monkeypatch.setattr(agent_runtime, 'get_client', lambda: SimpleNamespace(aio=SimpleNamespace(models=Models())))
    handler = ChatHandler(service.knowledge)

    async def route(request):
        return await handler.handle(request, [])

    service.router.route.side_effect = route
    await receive(service, update(140, 'Send the sample song'))
    assert len(calls) == tool_rounds + 1
    assert music_context.get() is None
    rows = service.db.execute('SELECT * FROM outbox ORDER BY id').fetchall()
    assert len(rows) == 2 and json.loads(rows[0]['audio'])[0]['id'] == songs[0].id
    assert rows[1]['text'] == 'Your song is queued.'
