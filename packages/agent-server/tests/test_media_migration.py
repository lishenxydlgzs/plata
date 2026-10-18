"""Migrate real storage shapes using synthetic data, preserving graph identity/history."""
import hashlib
from copy import deepcopy

import pytest

from agent_server import media
from agent_server.cc_catalog import write_manifest, MANIFEST
from agent_server.knowledge import KnowledgeStore
from agent_server.media_search import search_catalog


@pytest.fixture
def legacy(tmp_path, monkeypatch):
    root = tmp_path / 'audio'; folder = root / 'curated'; folder.mkdir(parents=True)
    monkeypatch.setattr(media, 'MEDIA_DIR', root)
    filename = 'curated/sample__abcdefghijk.mp3'
    (root / filename).write_bytes(b'audio')
    manifest = {'version': 1, 'cc_mode': True, 'replace_legacy_cc': False, 'tracks': {
        'abcdefghijk': {'video_id': 'abcdefghijk', 'title': 'CC Cycle 3 Week 4 Science',
            'filename': 'sample__abcdefghijk.mp3', 'subjects': ['science'], 'cycles': [3],
            'weeks': [4], 'all_cycles': False, 'classification_status': 'error',
            'classification_error': 'Sample classifier failure',
            'classification': {'input_hash': 'sample-hash', 'classified_at': '2026-01-01T00:00:00Z'}}}}
    write_manifest(folder, manifest)
    knowledge = KnowledgeStore(); knowledge.connect()
    song_id = knowledge.upsert_media('curated/sample__abcdefghijk.mp3', 'Old title', filename)
    playlist = knowledge.store.create_entity('playlist', 'CC Cycle3 Week 4',
                                             {'playlist_id': 'cc_cycle3_week_4', 'track_count': 1})
    knowledge.store.set_identifier(playlist.id, 'playlist_id', 'cc_cycle3_week_4')
    knowledge.store.create_link('contains', playlist.id, song_id, {'position': 0})
    session = knowledge.store.create_entity('playback_session', 'Sample progress', {
        'playlist_id': 'cc_cycle3_week_4', 'track_files': [filename], 'next_track_index': 1,
        'track_count': 1, 'status': 'completed'})
    knowledge.store.create_link('uses', session.id, playlist.id)
    note = knowledge.store.create_entity('message', 'Sample request')
    knowledge.store.create_link('triggered', note.id, song_id)
    yield knowledge, folder, song_id, playlist, session, manifest
    knowledge.store._db.close()


def test_migration_reuses_ids_links_errors_and_completed_progress(legacy):
    knowledge, folder, song_id, playlist, session, _ = legacy
    original = (folder / MANIFEST).read_bytes()
    knowledge.sync_media_catalog()
    catalog = knowledge.media_catalog
    song = catalog.resolve(song_id)
    assert song.properties['classification_status'] == 'error'
    assert song.properties['classification']['input_hash'] == 'sample-hash'
    assert catalog.members(playlist.id)[0].id == song_id
    state = knowledge.store.get_entity(session.id).properties
    assert state['playlist_id'] == playlist.id and state['next_track_index'] == 1
    assert state['track_files'] == session.properties['track_files']
    assert state['status'] == 'completed'
    assert knowledge.begin_playback(playlist.id, 1, 'resume') == {'session_id': session.id, 'start_index': 1}
    assert any(l.relationship_type == 'triggered' and l.to_entity == song_id for l in knowledge.store.get_all_links())
    assert catalog.resolve('curated/sample__abcdefghijk.mp3', 'media').id == song_id
    old_hash_id = 'song:' + hashlib.sha256(song.properties['filename'].encode()).hexdigest()
    assert catalog.resolve(old_hash_id, 'media').id == song_id
    knowledge.sync_media_catalog()
    assert len([s for s in catalog.songs() if s.properties['filename'] == song.properties['filename']]) == 1
    assert (folder / MANIFEST).read_bytes() == original


def test_restart_ignores_stale_manifest_and_preserves_database_edits(legacy):
    knowledge, folder, song_id, _, _, manifest = legacy
    knowledge.sync_media_catalog()
    knowledge.media_catalog.save(knowledge.store.get_entity(song_id), name='Database title',
                                 title='Database title', subjects=['history'], weeks=[5])
    stale = deepcopy(manifest)
    stale['tracks']['abcdefghijk']['title'] = 'Stale external title'
    write_manifest(folder, stale)
    restarted = KnowledgeStore(); restarted.connect(); restarted.sync_media_catalog()
    song = restarted.store.get_entity(song_id)
    assert song.name == 'Database title' and song.properties['weeks'] == [5]
    assert restarted.media_catalog.members('cc_cycle3_week_5')[0].id == song_id
    assert restarted.media_catalog.members('cc_cycle3_week_4') == []
    restarted.store._db.close()


def test_migration_rolls_back_atomically_and_can_retry(legacy, monkeypatch):
    knowledge, _, _, _, _, _ = legacy
    before = knowledge.get_graph_snapshot()
    real = knowledge.sync_media_associations
    def fail(*args, **kwargs):
        raise RuntimeError('Sample projection failure')
    monkeypatch.setattr(knowledge, 'sync_media_associations', fail)
    with pytest.raises(RuntimeError, match='Sample projection failure'):
        knowledge.sync_media_catalog()
    assert knowledge.get_graph_snapshot() == before
    table = knowledge.store._db.execute("SELECT 1 FROM sqlite_master WHERE name='media_catalog_version'").fetchone()
    assert not table
    monkeypatch.setattr(knowledge, 'sync_media_associations', real)
    knowledge.sync_media_catalog()
    assert knowledge.store._db.execute('SELECT version FROM media_catalog_version').fetchone()[0] == 1


def test_missing_and_restored_files_keep_identity_and_metadata(legacy):
    knowledge, folder, song_id, playlist, _, _ = legacy
    knowledge.sync_media_catalog()
    path = folder / 'sample__abcdefghijk.mp3'
    path.unlink()
    knowledge.sync_media_catalog()
    song = knowledge.store.get_entity(song_id)
    assert song.properties['available'] is False
    assert knowledge.media_catalog.members(playlist.id, available_only=False)[0].id == song_id
    assert search_catalog(knowledge, kind='song', entity_ids=[song_id])['results'] == []
    assert search_catalog(knowledge, available=False, entity_ids=[song_id])['results'][0]['id'] == song_id
    path.write_bytes(b'restored')
    knowledge.sync_media_catalog()
    assert knowledge.media_catalog.playable(knowledge.store.get_entity(song_id))
    assert knowledge.store.get_entity(song_id).properties['classification_error'] == 'Sample classifier failure'


def test_migration_preserves_source_playlist_order_and_custom_links(legacy):
    knowledge, folder, song_id, _, _, _ = legacy
    (folder / 'aaa.mp3').write_bytes(b'audio')
    other = knowledge.upsert_media('curated/aaa.mp3', 'Second song', 'curated/aaa.mp3')
    source = knowledge.store.create_entity('playlist', 'Curated', {'playlist_id': 'curated'})
    knowledge.store.set_identifier(source.id, 'playlist_id', 'curated')
    knowledge.store.create_link('contains', source.id, song_id, {'position': 0})
    knowledge.store.create_link('contains', source.id, other, {'position': 1})
    manual = knowledge.store.create_entity('playlist', 'Custom favorites', {'origin': 'manual'})
    knowledge.store.create_link('contains', manual.id, other, {'position': 0})
    knowledge.sync_media_catalog()
    assert [s.id for s in knowledge.media_catalog.members(source.id)] == [song_id, other]
    assert [s.id for s in knowledge.media_catalog.members(manual.id)] == [other]
    knowledge.sync_media_catalog()
    assert [s.id for s in knowledge.media_catalog.members(source.id)] == [song_id, other]


def test_migration_retires_stale_generated_groups(legacy):
    knowledge, _, song_id, _, _, _ = legacy
    stale = knowledge.store.create_entity('playlist', 'Old week', {'playlist_id': 'cc_cycle3_week_9', 'track_count': 1})
    knowledge.store.set_identifier(stale.id, 'playlist_id', 'cc_cycle3_week_9')
    knowledge.store.create_link('contains', stale.id, song_id, {'position': 0})
    knowledge.sync_media_catalog()
    assert knowledge.media_catalog.members(stale.id) == []
    assert not knowledge.store.get_entity(stale.id).properties['active']


def test_missing_mount_does_not_commit_migration_marker(tmp_path, monkeypatch):
    monkeypatch.setattr(media, 'MEDIA_DIR', tmp_path / 'not-mounted')
    knowledge = KnowledgeStore(); knowledge.connect(); knowledge.sync_media_catalog()
    assert knowledge.store._db.execute('SELECT version FROM media_catalog_version').fetchall() == []
    media.MEDIA_DIR.mkdir()
    knowledge.sync_media_catalog()
    assert knowledge.store._db.execute('SELECT version FROM media_catalog_version').fetchone()[0] == 1
    knowledge.store._db.close()


def test_rebuild_preserves_order_when_legacy_source_and_cc_view_share_alias(legacy):
    knowledge, _, song_id, _, _, _ = legacy
    backup = media.MEDIA_DIR / 'cc_cycle3' / 'week_4'
    backup.mkdir(parents=True)
    (backup / 'backup.mp3').write_bytes(b'backup')
    knowledge.sync_media_catalog()
    catalog = knowledge.media_catalog
    playlist = catalog.resolve('cc_cycle3_week_4')
    assert len(catalog.members(playlist.id)) == 2
    catalog.configure_source('curated', cc_mode=True, replace_legacy_cc=True)
    catalog.rebuild_groups()
    assert [s.id for s in catalog.members(playlist.id)] == [song_id]
    knowledge.sync_media_catalog()
    assert [s.id for s in catalog.members(playlist.id)] == [song_id]
    assert (backup / 'backup.mp3').read_bytes() == b'backup'
