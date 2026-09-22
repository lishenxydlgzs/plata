import json
from pathlib import Path

import pytest
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError

from agent_server import media, playlist_sync as module
from agent_server.playlist_sync import NewSyncJob, PlaylistSync, SyncSettings, sync_router

URL = 'https://www.youtube.com/playlist?list=PL_sample_playlist'


@pytest.fixture
def service(tmp_path, monkeypatch):
    monkeypatch.setattr(media, 'MEDIA_DIR', tmp_path / 'media')
    media.invalidate_playlist_cache()
    service = PlaylistSync(tmp_path / 'jobs.sqlite3')
    service.connect()
    yield service
    service.db.close()
    media.invalidate_playlist_cache()


def add(service):
    return service.add(NewSyncJob(url=URL, name='Sample playlist'))


@pytest.mark.parametrize('url', [
    'https://evil.example/playlist?list=PL_sample_playlist',
    'https://www.youtube.com.evil.example/playlist?list=PL_sample_playlist',
    'http://youtube.com/playlist?list=PL_sample_playlist',
    'https://youtube.com/playlist?list=../../bad',
])
def test_url_validation(url):
    with pytest.raises(ValidationError):
        NewSyncJob(url=url, name='Sample')


def test_persistence_and_recovery(service):
    job = add(service)
    with pytest.raises(HTTPException) as exc:
        add(service)
    assert exc.value.status_code == 409
    service.db.execute("UPDATE jobs SET state='running'")
    service.db.commit()
    other = PlaylistSync(service.db_path)
    other.connect()
    assert other.get(job['id'])['state'] == 'queued'
    assert other.get(job['id'])['next_run'] == 0
    other.db.close()


async def test_incremental_import_failure_retry_and_catalog(service, monkeypatch):
    job = add(service)
    calls = []
    failing = True

    async def downloader(*args):
        if '--flat-playlist' in args:
            return json.dumps({'entries': [
                {'id': 'abcdefghijk', 'title': 'Sample song'},
                {'id': 'lmnopqrstuv', 'title': '../Another song'},
            ]})
        calls.append(args[-1])
        if failing and 'lmnopqrstuv' in args[-1]:
            raise RuntimeError('Video unavailable')
        output = Path(args[args.index('-o') + 1].replace('%(ext)s', 'mp3'))
        output.write_bytes(b'test audio')
        return ''

    monkeypatch.setattr(module, 'run_downloader', downloader)
    assert media.get_playlist_catalog() == {}
    await service.sync(job)
    result = service.get(job['id'])
    assert result['state'] == 'error'
    assert result['imported'] == 1
    assert result['next_run'] > result['last_run']
    assert len(next(iter(media.get_playlist_catalog().values()))) == 1
    failing = False
    await service.sync(result)
    assert service.get(job['id'])['state'] == 'idle'
    assert service.get(job['id'])['imported'] == 1
    assert len(next(iter(media.get_playlist_catalog().values()))) == 2
    assert len(calls) == 3
    assert not any('..' in p.name for p in media.MEDIA_DIR.rglob('*.mp3'))
    await service.sync(service.get(job['id']))
    assert service.get(job['id'])['imported'] == 0
    assert len(calls) == 3


async def test_api_settings_and_queue(service):
    app = FastAPI()
    app.include_router(sync_router(service))
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
        response = await client.post('/media/sync-jobs', json={'url': URL, 'name': 'Sample'})
        assert response.status_code == 201
        job_id = response.json()['id']
        base = f'/media/sync-jobs/{job_id}'
        assert (await client.get('/media/sync-jobs')).json()[0]['id'] == job_id
        assert (await client.put(base, json={'interval_hours': 0})).status_code == 422
        assert (await client.put(base, json={'interval_hours': 12, 'enabled': False})).status_code == 200
        assert (await client.post(base + '/sync')).status_code == 409
        await client.put(base, json={'interval_hours': 12, 'enabled': True})
        assert (await client.post(base + '/sync')).status_code == 202
        service.db.execute("UPDATE jobs SET state='running'")
        service.db.commit()
        assert (await client.post(base + '/sync')).status_code == 409
        assert (await client.post('/media/sync-jobs/missing/sync')).status_code == 404


async def test_worker_skips_disabled_and_future_jobs(service, monkeypatch):
    job = add(service)
    service.settings(job['id'], SyncSettings(interval_hours=24, enabled=False))
    calls = []

    async def sync(job):
        calls.append(job)

    async def sleep(seconds):
        raise module.asyncio.CancelledError

    monkeypatch.setattr(service, 'sync', sync)
    monkeypatch.setattr(module.asyncio, 'sleep', sleep)
    with pytest.raises(module.asyncio.CancelledError):
        await service.worker()
    assert calls == []
    service.settings(job['id'], SyncSettings(interval_hours=24))
    service.db.execute('UPDATE jobs SET next_run=?', (module.time.time() + 3600,))
    with pytest.raises(module.asyncio.CancelledError):
        await service.worker()
    assert calls == []
    service.queue(job['id'])
    with pytest.raises(module.asyncio.CancelledError):
        await service.worker()
    assert len(calls) == 1


def test_staging_is_not_playable(service):
    staging = media.MEDIA_DIR / '.sync-staging' / 'temp'
    staging.mkdir(parents=True)
    (staging / 'audio.mp3').write_bytes(b'incomplete')
    assert media.get_playlist_catalog() == {}


async def test_failed_conversion_never_publishes_partial_audio(service, monkeypatch):
    job = add(service)

    async def downloader(*args):
        if '--flat-playlist' in args:
            return json.dumps({'entries': [{'id': 'abcdefghijk', 'title': 'Sample'}]})
        Path(args[args.index('-o') + 1].replace('%(ext)s', 'mp3')).write_bytes(b'partial')
        raise RuntimeError('Conversion failed')

    monkeypatch.setattr(module, 'run_downloader', downloader)
    await service.sync(job)
    assert service.get(job['id'])['state'] == 'error'
    assert not list(media.MEDIA_DIR.rglob('*.mp3'))
    assert media.get_playlist_catalog() == {}


async def test_cancel_terminates_downloader_process_group(monkeypatch):
    from unittest.mock import AsyncMock, Mock

    process = Mock(pid=12345)
    process.communicate = AsyncMock(side_effect=module.asyncio.CancelledError)
    process.wait = AsyncMock()
    spawn = AsyncMock(return_value=process)
    kill = Mock()
    monkeypatch.setattr(module.asyncio, 'create_subprocess_exec', spawn)
    monkeypatch.setattr(module.os, 'killpg', kill)
    with pytest.raises(module.asyncio.CancelledError):
        await module.run_downloader('--no-playlist', 'https://www.youtube.com/watch?v=abcdefghijk')
    kill.assert_called_once_with(12345, module.signal.SIGKILL)
    process.wait.assert_awaited_once()
    assert spawn.call_args.kwargs['start_new_session'] is True
