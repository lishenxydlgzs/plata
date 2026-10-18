"""Catalog path safety applies to discovery and execution."""
from agent_server import media
from agent_server.knowledge import KnowledgeStore


def test_discovery_skips_unsafe_and_hidden_files(tmp_path, monkeypatch):
    week = tmp_path / 'cc_cycle3' / 'week_1'
    week.mkdir(parents=True)
    (week / 'safe.mp3').write_bytes(b'audio')
    (week / 'unsafe..mp3').write_bytes(b'audio')
    hidden = tmp_path / '.staging'
    hidden.mkdir()
    (hidden / 'incomplete.mp3').write_bytes(b'audio')
    monkeypatch.setattr(media, 'MEDIA_DIR', tmp_path)
    knowledge = KnowledgeStore(); knowledge.connect(); knowledge.sync_media_catalog()
    assert [s.properties['filename'] for s in knowledge.media_catalog.songs()] == ['cc_cycle3/week_1/safe.mp3']
    knowledge.store._db.close()


def test_execution_rejects_escaping_symlink_and_unsafe_graph_paths(tmp_path, monkeypatch):
    root = tmp_path / 'library'; root.mkdir()
    (root / 'sample.mp3').write_bytes(b'audio')
    outside = tmp_path / 'outside.mp3'; outside.write_bytes(b'outside')
    monkeypatch.setattr(media, 'MEDIA_DIR', root)
    knowledge = KnowledgeStore(); knowledge.connect(); knowledge.sync_media_catalog()
    song = knowledge.media_catalog.resolve('sample', 'media')
    (root / 'sample.mp3').unlink()
    (root / 'sample.mp3').symlink_to(outside)
    assert not knowledge.media_catalog.playable(song)
    knowledge.media_catalog.save(song, filename='../outside.mp3', available=True)
    assert not knowledge.media_catalog.playable(knowledge.store.get_entity(song.id))
    knowledge.store._db.close()
