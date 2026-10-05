"""CC imports classify titles and replace the legacy catalog without deleting it."""

import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from agent_server import media, playlist_sync as module
from agent_server import cc_catalog
from agent_server.cc_catalog import read_manifest
from agent_server.playlist_sync import NewSyncJob, PlaylistSync, SyncSettings, sync_router


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setattr(media, 'MEDIA_DIR', tmp_path / 'media')
    media.invalidate_playlist_cache()
    legacy = media.MEDIA_DIR / 'cc_cycle3' / 'week_5' / 'old_science.mp3'
    legacy.parent.mkdir(parents=True)
    legacy.write_bytes(b'backup')
    unrelated = media.MEDIA_DIR / 'bedtime' / 'sample.mp3'
    unrelated.parent.mkdir()
    unrelated.write_bytes(b'audio')
    service = PlaylistSync(tmp_path / 'jobs.sqlite3')
    service.connect()
    job = service.add(NewSyncJob(url='https://youtube.com/playlist?list=PL_sample_playlist',
                                name='Sample curated CC', cc_mode=True,
                                replace_legacy_cc=True))
    entries = [{'id': 'abcdefghijk', 'title': 'Cycle 3 Week 5 Science'},
               {'id': 'lmnopqrstuv', 'title': 'Week 5 Grammar'},
               {'id': '01234567890', 'title': 'Sample mystery song'}]
    calls = []
    failures = set()

    async def download(*args):
        if '--flat-playlist' in args:
            return json.dumps({'entries': entries})
        video_id = args[-1].split('=')[-1]
        calls.append(video_id)
        if video_id in failures:
            raise RuntimeError('Sample unavailable video')
        Path(args[args.index('-o') + 1].replace('%(ext)s', 'mp3')).write_bytes(b'audio')
        return ''

    classifications = {
        'Cycle 3 Week 5 Science': (['science'], [3], [5]),
        'Week 5 Grammar': (['english'], [], [5]),
        'Sample mystery song': ([], [], []),
        'Cycle 3 Week 5 Geography': (['geography'], [3], [5]),
        'Cycle 3 Week 6 Math': (['math'], [3], [6]),
        'Week 6 Math': (['math'], [], [6]),
    }
    async def classify(prompt, history, user_text, **kwargs):
        results = []
        for item in json.loads(user_text)['tracks']:
            subjects, cycles, weeks = classifications[item['title']]
            results.append(dict(video_id=item['video_id'], subjects=subjects, cycles=cycles,
                all_cycles=not cycles, weeks=weeks, tags=[], topics=[], entities=[],
                confidence=0.9 if subjects else 0.1, needs_review=not bool(subjects),
                explanation='Synthetic model classification.'))
        return {'tracks': results}
    monkeypatch.setattr(cc_catalog, 'generate_chat_json', classify)
    monkeypatch.setattr(cc_catalog, 'MIN_BATCH_INTERVAL', 0)
    monkeypatch.setattr(module, 'run_downloader', download)
    yield service, job, entries, calls, failures, legacy
    service.db.close()
    media.invalidate_playlist_cache()


async def test_incremental_groups_refresh_titles_and_preserve_backup(setup):
    service, job, entries, calls, _, legacy = setup
    await service.sync(job)
    assert legacy.read_bytes() == b'backup'
    catalog = media.get_playlist_catalog()
    assert len(catalog['cc_cycle3_week_5']) == 2
    assert catalog['cc_cycle3_week_5_science'][0]['title'] == entries[0]['title']
    assert len(catalog['cc_unclassified']) == 1
    assert 'bedtime' in catalog
    for cycle in (1, 2, 3):
        assert catalog[f'cc_cycle{cycle}_week_5_english'][0]['title'] == 'Week 5 Grammar'
    assert 'cc_cycle1_week_5_science' not in catalog
    assert not any(t['file'].startswith('cc_cycle3/') for tracks in catalog.values() for t in tracks)
    assert media.resolve_playlist('cc_english')[0]['media_content_id'].endswith('.mp3')
    assert len(calls) == 3

    # Same video with corrected title must move subjects without another download.
    entries[0]['title'] = 'Cycle 3 Week 5 Geography'
    entries.append({'id': 'too_short', 'title': 'Cycle 3 Week 6 Math'})  # invalid ID ignored
    entries.append({'id': 'abcdef12345', 'title': 'Cycle 3 Week 6 Math'})
    await service.sync(service.get(job['id']))
    catalog = media.get_playlist_catalog()
    assert 'cc_science' not in catalog
    assert catalog['cc_geography'][0]['title'] == entries[0]['title']
    assert len(catalog['cc_cycle3_week_6_math']) == 1
    assert len(calls) == 4
    # Folder names contain CC but subject filtering must use the refreshed title.
    source_id = job['folder'].lower()
    assert media.resolve_playlist(source_id + '_science') is None
    assert media.resolve_playlist(source_id + '_geography')
    media.invalidate_playlist_cache()
    assert 'cc_geography' in media.get_playlist_catalog()  # manifest survives cache/restart


async def test_failed_first_import_does_not_hide_legacy_but_later_failure_keeps_cutover(setup):
    service, job, entries, _, failures, legacy = setup
    failures.add(entries[1]['id'])
    await service.sync(job)
    assert service.get(job['id'])['state'] == 'error'
    assert any(t['file'] == str(legacy.relative_to(media.MEDIA_DIR))
               for t in media.get_playlist_catalog()['cc_cycle3_week_5'])
    failures.clear()
    await service.sync(service.get(job['id']))
    assert read_manifest(media.MEDIA_DIR / job['folder'])['replace_legacy_cc']
    entries.append({'id': 'abcdef12345', 'title': 'Week 6 Math'})
    failures.add('abcdef12345')
    await service.sync(service.get(job['id']))
    assert service.get(job['id'])['state'] == 'error'
    assert all(not t['file'].startswith('cc_cycle3/')
               for t in media.get_playlist_catalog()['cc_cycle3_week_5'])
    assert legacy.exists()


async def test_empty_playlist_keeps_legacy_visible(setup):
    service, job, entries, _, _, _ = setup
    entries.clear()
    await service.sync(job)
    assert len(media.get_playlist_catalog()['cc_cycle3_week_5']) == 1
    assert not read_manifest(media.MEDIA_DIR / job['folder'])['replace_legacy_cc']


async def test_api_preserves_cc_settings_and_can_restore_legacy(setup):
    service, job, _, calls, _, legacy = setup
    await service.sync(job)
    app = FastAPI()
    app.include_router(sync_router(service))
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
        base = f'/media/sync-jobs/{job["id"]}'
        catalog = (await client.get(base + '/tracks')).json()
        assert catalog['replacement_active']
        assert catalog['tracks'][0]['subjects'] == ['science']
        result = await client.put(base, json={'interval_hours': 12})
        assert result.status_code == 200
        assert result.json()['cc_mode']
        assert result.json()['replace_legacy_cc']
        assert (await client.put(base, json={'interval_hours': 12, 'cc_mode': False})).status_code == 422
        result = await client.put(base, json={'interval_hours': 12, 'replace_legacy_cc': False})
        assert result.status_code == 200
        await service.sync(result.json())
        assert len(calls) == 3
        assert any(t['file'] == str(legacy.relative_to(media.MEDIA_DIR))
                   for t in media.get_playlist_catalog()['cc_cycle3_week_5'])
        # Shared songs remain in each cycle's weekly group.
        assert len(media.get_playlist_catalog()['cc_cycle3_week_5']) == 3


async def test_existing_generic_job_can_enable_cc_without_redownloading(setup):
    service, job, _, calls, _, _ = setup
    service.settings(job['id'], SyncSettings(interval_hours=24, cc_mode=False,
                                            replace_legacy_cc=False))
    await service.sync(service.get(job['id']))
    assert 'cc' not in media.get_playlist_catalog()
    service.settings(job['id'], SyncSettings(interval_hours=24, cc_mode=True, replace_legacy_cc=True))
    await service.sync(service.get(job['id']))
    assert len(calls) == 3
    assert len(media.get_playlist_catalog()['cc']) == 3


async def test_create_cc_job_api_and_persist_settings(tmp_path):
    service = PlaylistSync(tmp_path / 'jobs.sqlite3')
    service.connect()
    app = FastAPI()
    app.include_router(sync_router(service))
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
        body = {'name': 'Sample CC', 'url': 'https://youtube.com/playlist?list=PL_sample_playlist',
                'cc_mode': True, 'replace_legacy_cc': True, 'interval_hours': 12}
        response = await client.post('/media/sync-jobs', json=body)
        assert response.status_code == 201
        assert response.json()['cc_mode']
        assert response.json()['replace_legacy_cc']
        assert (await client.post('/media/sync-jobs', json={**body, 'cc_mode': False})).status_code == 422
    service.db.close()
    restarted = PlaylistSync(service.db_path)
    restarted.connect()
    job = restarted.get('PL_sample_playlist')
    assert job['cc_mode'] and job['replace_legacy_cc']
    assert job['interval_hours'] == 12
    restarted.db.close()
