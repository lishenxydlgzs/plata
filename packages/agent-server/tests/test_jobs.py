import asyncio
import logging
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient

from agent_server.job_history import active_run
from agent_server.maintenance import MaintenanceJob
from agent_server.playlist_sync import (
    MAINTENANCE_ID, NewSyncJob, PlaylistSync, SyncSettings, jobs_router, sync_router,
)


@pytest.fixture
def service(tmp_path):
    maintenance = AsyncMock()
    maintenance.run_now.return_value = {'iterations': 1, 'total_actions': 2}
    service = PlaylistSync(tmp_path / 'jobs.sqlite3', maintenance=maintenance)
    service.connect()
    yield service
    service.db.close()


async def test_success_failure_and_task_scoped_logs(service, caplog):
    caplog.set_level(logging.INFO)
    logger = logging.getLogger('agent_server.maintenance')
    ready, finish = asyncio.Event(), asyncio.Event()

    async def maintain():
        logger.info('Inspected sample facts')
        ready.set()
        await finish.wait()
        return {'total_actions': 2}

    service.maintenance.run_now.side_effect = maintain
    task = asyncio.create_task(service.sync(service.get(MAINTENANCE_ID), trigger='manual'))
    await ready.wait()
    logger.info('Unrelated task log must stay out of execution history')
    run = service.history.runs(MAINTENANCE_ID)[0]
    assert run['status'] == 'running'
    with pytest.raises(HTTPException) as exc:
        await service.sync(service.get(MAINTENANCE_ID))
    assert exc.value.status_code == 409
    finish.set()
    await task
    detail = service.history.detail(MAINTENANCE_ID, run['id'])
    assert detail['status'] == 'succeeded'
    assert detail['trigger'] == 'manual'
    assert 'Applied 2 improvements' in detail['summary']
    assert any('Inspected' in row['message'] for row in detail['logs'])
    assert not any('Unrelated' in row['message'] for row in detail['logs'])
    service.maintenance.run_now.side_effect = RuntimeError('Sample provider failure')
    await service.sync(service.get(MAINTENANCE_ID))
    assert service.history.runs(MAINTENANCE_ID)[0]['status'] == 'failed'
    assert service.get(MAINTENANCE_ID)['error'] == 'Sample provider failure'
    assert service.get(MAINTENANCE_ID)['next_run'] > service.get(MAINTENANCE_ID)['last_run']


async def test_cancellation_and_crash_recovery(service):
    started = asyncio.Event()

    async def maintain():
        started.set()
        await asyncio.Event().wait()

    service.maintenance.run_now.side_effect = maintain
    task = asyncio.create_task(service.sync(service.get(MAINTENANCE_ID)))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert service.history.runs(MAINTENANCE_ID)[0]['status'] == 'interrupted'
    assert service.get(MAINTENANCE_ID)['state'] == 'queued'
    assert service._active is False
    # Simulate an ungraceful termination where cleanup did not run.
    run_id = service.history.begin(MAINTENANCE_ID, 'scheduled')
    with service.db:
        service.db.execute("UPDATE jobs SET state='running' WHERE id=?", (MAINTENANCE_ID,))
    restarted = PlaylistSync(service.db_path, maintenance=service.maintenance)
    restarted.connect()
    assert restarted.history.detail(MAINTENANCE_ID, run_id)['status'] == 'interrupted'
    assert restarted.get(MAINTENANCE_ID)['next_run'] == 0
    restarted.db.close()


def test_bounded_history_and_logs(service):
    for _ in range(105):
        run_id = service.history.begin(MAINTENANCE_ID, 'manual')
        token = active_run.set((service.history, run_id))
        service.history.emit(logging.makeLogRecord({'msg': 'Sample log'}))
        active_run.reset(token)
        service.history.finish(run_id, 'succeeded')
    assert len(service.history.runs(MAINTENANCE_ID, limit=200)) == 100
    assert service.db.execute('SELECT count(*) FROM job_logs').fetchone()[0] == 100
    token = active_run.set((service.history, run_id))
    for _ in range(505):
        service.history.emit(logging.makeLogRecord({'msg': 'x' * 3000}))
    active_run.reset(token)
    logs = service.history.detail(MAINTENANCE_ID, run_id)['logs']
    assert len(logs) == 500
    assert all(len(row['message']) <= 2000 for row in logs)


async def test_api_persistence_pagination_and_playlist_compatibility(service):
    app = FastAPI()
    app.include_router(jobs_router(service))
    app.include_router(sync_router(service))
    job = service.add(NewSyncJob(url='https://youtube.com/playlist?list=PL_sample_playlist', name='Sample playlist'))
    for _ in range(22):
        run_id = service.history.begin(MAINTENANCE_ID, 'manual')
        service.history.finish(run_id, 'succeeded')
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
        jobs = (await client.get('/api/jobs')).json()
        assert {job['kind'] for job in jobs} == {'maintenance', 'playlist'}
        playlists = (await client.get('/media/sync-jobs')).json()
        assert len(playlists) == 1
        assert playlists[0]['id'] == job['id']
        base = f'/api/jobs/{MAINTENANCE_ID}'
        response = await client.put(base, json={'interval_hours': 168, 'enabled': False})
        assert response.status_code == 200
        assert (await client.post(base + '/run')).status_code == 409
        restarted = PlaylistSync(service.db_path, maintenance=service.maintenance)
        restarted.connect()
        assert restarted.get(MAINTENANCE_ID)['interval_hours'] == 168
        assert not restarted.get(MAINTENANCE_ID)['enabled']
        restarted.db.close()
        assert (await client.put(base, json={'interval_hours': -1})).status_code == 422
        first = (await client.get(base + '/runs')).json()
        second = (await client.get(base + f'/runs?before={first[-1]["id"]}')).json()
        assert len(first) == 20 and len(second) == 2
        assert not {row['id'] for row in first} & {row['id'] for row in second}
        assert (await client.get(base + f'/runs/{run_id}')).status_code == 200
        assert (await client.get(f'/api/jobs/{job["id"]}/runs/{run_id}')).status_code == 404
        assert (await client.get('/api/jobs/unknown/runs')).status_code == 404
        await client.put(base, json={'interval_hours': 12, 'enabled': True})
        queued = await client.post(base + '/run')
        assert queued.status_code == 202
        assert queued.json()['queued_trigger'] == 'manual'


async def test_maintenance_does_not_swallow_llm_failure(monkeypatch):
    job = MaintenanceJob(None)
    monkeypatch.setattr(job, '_build_snapshot', lambda: {'facts': [1], 'facts_text': 'Sample', 'topics_text': ''})
    monkeypatch.setattr(job, '_call_llm', AsyncMock(side_effect=RuntimeError('Sample failure')))
    with pytest.raises(RuntimeError, match='Sample failure'):
        await job.run_now()


def test_schedule_change_does_not_trigger_immediate_run(service):
    import time
    before = time.time()
    updated = service.settings(MAINTENANCE_ID, SyncSettings(interval_hours=12))
    assert updated['next_run'] >= before + 12 * 3600


def test_existing_playlist_database_migrates_without_losing_schedule(tmp_path):
    import sqlite3
    db_path = tmp_path / 'existing.sqlite3'
    db = sqlite3.connect(db_path)
    db.execute('''CREATE TABLE jobs (
        id TEXT PRIMARY KEY, url TEXT NOT NULL, folder TEXT NOT NULL,
        interval_hours INTEGER NOT NULL, enabled INTEGER NOT NULL DEFAULT 1,
        next_run REAL NOT NULL DEFAULT 0, last_run REAL,
        state TEXT NOT NULL DEFAULT 'queued', imported INTEGER NOT NULL DEFAULT 0,
        error TEXT)''')
    db.execute("INSERT INTO jobs(id,url,folder,interval_hours,next_run) VALUES(?,?,?,?,?)",
               ('PL_sample_playlist', 'https://youtube.com/playlist?list=PL_sample_playlist', 'Sample', 48, 123456))
    db.commit()
    db.close()
    service = PlaylistSync(db_path, maintenance=AsyncMock())
    service.connect()
    job = service.get('PL_sample_playlist')
    assert job['interval_hours'] == 48
    assert job['next_run'] == 123456
    assert job['folder'] == 'Sample'
    assert job['queued_trigger'] == 'scheduled'
    assert service.get(MAINTENANCE_ID)['interval_hours'] == 24
    assert service.history.runs(job['id']) == []
    service.db.close()


async def test_jobs_page_and_workspace_navigation():
    from agent_server.app import app
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
        response = await client.get('/jobs')
        assert response.status_code == 200
        assert response.headers['cache-control'] == 'no-store'
        assert 'Execution history' in response.text
        assert 'Execution logs' in response.text
        assert 'href="/jobs"' in (await client.get('/graph')).text
