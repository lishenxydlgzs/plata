"""Search the ontology with typed IDs, filters, bounds, and ordered membership."""
import pytest

from agent_server import media
from agent_server.cc_catalog import write_manifest
from agent_server.knowledge import KnowledgeStore
from agent_server.media_search import search_catalog, media_search_tool


@pytest.fixture
def library(tmp_path, monkeypatch):
    monkeypatch.setattr(media, 'MEDIA_DIR', tmp_path)
    (tmp_path / 'sample_song.mp3').write_bytes(b'audio')
    folder = tmp_path / 'imported'; folder.mkdir()
    (folder / 'science.mp3').write_bytes(b'audio')
    write_manifest(folder, {'version': 1, 'cc_mode': True, 'tracks': {
        'abcdefghijk': {'filename': 'science.mp3', 'title': 'CC Cycle 3 Week 4 Science',
                        'subjects': ['science'], 'cycles': [3], 'weeks': [4], 'tags': ['biology']},
    }})
    knowledge = KnowledgeStore(); knowledge.connect(); knowledge.sync_media_catalog()
    yield knowledge, folder
    knowledge.store._db.close()


def test_search_distinguishes_song_playlist_with_same_entity_identity(library):
    knowledge, _ = library
    result = search_catalog(knowledge, query='CC week 4 science')
    assert {r['kind'] for r in result['results']} == {'song', 'playlist'}
    songs = [r for r in result['results'] if r['kind'] == 'song']
    assert len(songs) == 1
    playlist = knowledge.media_catalog.resolve('cc_cycle3_week_4')
    assert search_catalog(knowledge, playlist_id=playlist.id, kind='song')['results'] == songs
    assert search_catalog(knowledge, query='cycle3 week 4', kind='song')['results'] == songs
    assert 'file' not in songs[0]
    assert knowledge.store.get_entity(songs[0]['id']).entity_type == 'media'
    knowledge.sync_media_catalog()
    assert search_catalog(knowledge, query='science', kind='song')['results'] == songs
    assert knowledge.media_catalog.resolve(songs[0]['id']).properties['filename'] == 'imported/science.mp3'


def test_metadata_filters_and_graph_edits_are_authoritative(library):
    knowledge, _ = library
    song = knowledge.media_catalog.resolve('sample_song', 'media')
    knowledge.media_catalog.save(song, name='Sample shared song', subjects=['history'], all_cycles=True, weeks=[4], tags=['sample'])
    result = search_catalog(knowledge, kind='song', subject='history', cycle=2, week=4, tag='sample')
    assert [r['id'] for r in result['results']] == [song.id]
    assert search_catalog(knowledge, kind='song', subject='science', week=5)['total'] == 0
    assert search_catalog(knowledge, kind='playlist', cycle=3, week=4, subject='science')['total'] > 0
    knowledge.sync_media_catalog()
    assert search_catalog(knowledge, entity_ids=[song.id])['results'][0]['title'] == 'Sample shared song'


def test_failed_classification_remains_searchable(library):
    knowledge, _ = library
    song = next(s for s in knowledge.media_catalog.songs() if 'imported/' in s.properties['filename'])
    knowledge.media_catalog.save(song, subjects=[], cycles=[], weeks=[], classification_status='error')
    knowledge.media_catalog.rebuild_groups()
    found = search_catalog(knowledge, query='week 4 science', kind='song')['results'][0]
    assert found['classification_status'] == 'error'
    assert search_catalog(knowledge, playlist_id='cc_unclassified')['results'] == [found]
    assert search_catalog(knowledge, playlist_id='cc_cycle3_week_4')['results'] == []


def test_bounds_pagination_and_missing_entries(library):
    knowledge, folder = library
    first = search_catalog(knowledge, limit=1)
    second = search_catalog(knowledge, limit=1, offset=first['next_offset'])
    assert first['results'] != second['results']
    assert search_catalog(knowledge, query='notpresent')['total'] == 0
    for args in ({'limit': 21}, {'limit': True}, {'offset': -1}, {'query': 'x' * 301},
                 {'kind': 'invalid'}, {'playlist_id': '../private'}, {'cycle': 4}, {'week': 25}):
        assert 'error' in search_catalog(knowledge, **args)
    song_id = search_catalog(knowledge, kind='song', query='science')['results'][0]['id']
    (folder / 'science.mp3').unlink()
    knowledge.sync_media_catalog()
    assert search_catalog(knowledge, kind='song', query='science')['results'] == []
    assert search_catalog(knowledge, available=False, entity_ids=[song_id])['results'][0]['available'] is False
    assert not knowledge.media_catalog.playable(knowledge.store.get_entity(song_id))


def test_membership_browse_keeps_order(library):
    knowledge, _ = library
    catalog = knowledge.media_catalog
    songs = catalog.songs()
    playlist = catalog.playlist('sample_order', origin='manual')
    catalog.set_members(playlist, list(reversed(songs)))
    result = search_catalog(knowledge, playlist_id=playlist.id, limit=1)
    more = search_catalog(knowledge, playlist_id=playlist.id, offset=result['next_offset'])
    assert [r['id'] for r in result['results'] + more['results']] == [s.id for s in reversed(songs)]


async def test_tool_budget_is_per_turn_and_reads_do_not_write(library):
    knowledge, _ = library
    before = knowledge.store._db.total_changes
    tool = media_search_tool(knowledge)
    for _ in range(6):
        assert 'results' in await tool(query='science')
    assert 'error' in await tool()
    assert 'results' in await media_search_tool(knowledge)(query='science')
    assert knowledge.store._db.total_changes == before
