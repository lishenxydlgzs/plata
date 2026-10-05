"""The agent interprets titles; code validates, caches, and projects its results."""

import json
from copy import deepcopy

import pytest
from agent_server import cc_catalog as cc, media, knowledge
from agent_server.knowledge import KnowledgeStore


def result(video_id='abcdefghijk', **changes):
    return dict(video_id=video_id, subjects=['science'], cycles=[], all_cycles=True,
                weeks=[3], tags=['light'], topics=['optics'],
                entities=[{'kind': 'concept', 'name': 'Reflection'}],
                confidence=0.85, needs_review=False, explanation='The title describes light.', **changes)


@pytest.fixture
def library(tmp_path, monkeypatch):
    folder = tmp_path / 'media' / 'sample'
    folder.mkdir(parents=True)
    (folder / 'sample__abcdefghijk.mp3').write_bytes(b'audio')
    manifest = {'version': 1, 'cc_mode': True, 'replace_legacy_cc': False, 'tracks': {
        'abcdefghijk': {'video_id': 'abcdefghijk', 'title': 'A song about light',
                       'filename': 'sample__abcdefghijk.mp3', 'subjects': [], 'weeks': []}}}
    calls = []
    answer = {'tracks': [result()]}

    async def generate(prompt, history, text, **kwargs):
        assert 'untrusted source data' in prompt
        assert kwargs['max_output_tokens'] >= 4000
        calls.append(json.loads(text))
        return deepcopy(answer)

    monkeypatch.setattr(cc, 'generate_chat_json', generate)
    monkeypatch.setattr(cc, 'MIN_BATCH_INTERVAL', 0)
    monkeypatch.setattr(media, 'MEDIA_DIR', folder.parent)
    media.invalidate_playlist_cache()
    yield folder, manifest, calls, answer
    media.invalidate_playlist_cache()


async def test_agent_result_cached_and_title_changes_reclassify(library):
    folder, manifest, calls, answer = library
    assert await cc.enrich_manifest(folder, manifest, 'Sample playlist') == []
    track = manifest['tracks']['abcdefghijk']
    assert track['subjects'] == ['science']  # No literal subject word in the title.
    assert track['classification']['source_title'] == 'A song about light'
    assert track['classification_status'] == 'ready'
    assert len(calls) == 1
    assert await cc.enrich_manifest(folder, cc.read_manifest(folder), 'Sample playlist') == []
    assert len(calls) == 1
    track['title'] = 'A song about maps'
    answer['tracks'][0].update(subjects=['geography'], tags=['maps'], topics=['cartography'])
    assert await cc.enrich_manifest(folder, manifest, 'Sample playlist') == []
    assert len(calls) == 2
    assert track['subjects'] == ['geography']
    assert track['classification']['source_title'] == 'A song about maps'
    assert (folder / track['filename']).read_bytes() == b'audio'
    # Context changes also invalidate the cache.
    await cc.enrich_manifest(folder, manifest, 'Different playlist context')
    assert len(calls) == 3


@pytest.mark.parametrize('mutation', ['missing', 'extra', 'duplicate', 'bad-week', 'bad-scope', 'unknown-subject', 'bad-entity'])
async def test_invalid_batch_preserves_last_good_metadata_and_retries(library, mutation):
    folder, manifest, calls, answer = library
    await cc.enrich_manifest(folder, manifest, 'Sample')
    previous = deepcopy(manifest['tracks']['abcdefghijk'])
    manifest['tracks']['abcdefghijk']['title'] = 'Updated title'
    good = deepcopy(answer)
    if mutation == 'missing': answer['tracks'] = []
    elif mutation == 'extra': answer['tracks'].append(result(video_id='lmnopqrstuv'))
    elif mutation == 'duplicate': answer['tracks'].append(deepcopy(answer['tracks'][0]))
    elif mutation == 'bad-week': answer['tracks'][0]['weeks'] = [25]
    elif mutation == 'bad-scope': answer['tracks'][0]['cycles'] = [3]
    elif mutation == 'unknown-subject': answer['tracks'][0]['subjects'] = ['invented-subject']
    elif mutation == 'bad-entity': answer['tracks'][0]['entities'] = [{'kind': 'household_person', 'name': 'Sample'}]
    assert await cc.enrich_manifest(folder, manifest, 'Sample')
    track = cc.read_manifest(folder)['tracks']['abcdefghijk']
    assert track['classification_status'] == 'error'
    assert track['classification'] == previous['classification']
    assert track['subjects'] == previous['subjects']
    answer.clear(); answer.update(good)
    assert await cc.enrich_manifest(folder, manifest, 'Sample') == []
    assert manifest['tracks']['abcdefghijk']['classification_status'] == 'ready'
    assert len(calls) == 3


async def test_provider_failure_keeps_new_song_unclassified(library, monkeypatch):
    folder, manifest, _, _ = library
    async def fail(*args, **kwargs):
        raise RuntimeError('Sample provider unavailable')
    monkeypatch.setattr(cc, 'generate_chat_json', fail)
    assert await cc.enrich_manifest(folder, manifest, 'Sample')
    track = manifest['tracks']['abcdefghijk']
    assert track['subjects'] == []
    assert track['classification_status'] == 'error'
    assert 'classification' not in track
    assert media.get_playlist_catalog()['cc_unclassified']


async def test_batches_cache_and_migrate_legacy_metadata(library, monkeypatch):
    folder, manifest, _, _ = library
    calls = []
    for index in range(9):
        video_id = f'{index:011d}'
        filename = video_id + '.mp3'
        (folder / filename).write_bytes(b'audio')
        manifest['tracks'][video_id] = {'video_id': video_id, 'title': 'Sample title', 'filename': filename,
                                       'subjects': ['history'], 'cycle': 3, 'all_cycles': False, 'weeks': [1]}
    async def classify(prompt, history, text, **kwargs):
        inputs = json.loads(text)['tracks']
        calls.append(inputs)
        return {'tracks': [result(video_id=item['video_id']) for item in inputs]}
    monkeypatch.setattr(cc, 'generate_chat_json', classify)
    assert await cc.enrich_manifest(folder, manifest, 'Sample') == []
    assert [len(items) for items in calls] == [8, 2]
    assert all(track['subjects'] == ['science'] for track in manifest['tracks'].values())
    await cc.enrich_manifest(folder, cc.read_manifest(folder), 'Sample')
    assert len(calls) == 2


async def test_graph_associations_reuse_entities_and_reconcile_only_owned_links(library, monkeypatch, tmp_path):
    folder, manifest, _, answer = library
    await cc.enrich_manifest(folder, manifest, 'Sample')
    monkeypatch.setattr(knowledge, 'ONTOLOGY_DB_PATH', tmp_path / 'ontology.db')
    store = KnowledgeStore(); store.connect()
    # A pre-existing topic should be reused by classifier associations.
    topic = store.store.create_entity('topic', 'optics')
    store.sync_media_catalog()
    entity = store.store.get_entity_by_identifier('media_file', 'sample/sample__abcdefghijk.mp3')
    assert entity.properties['tags'] == ['light']
    links = [link for link in store.store.get_all_links() if link.from_entity == entity.id and link.relationship_type == 'classified_as']
    targets = {store.store.get_entity(link.to_entity).entity_type for link in links}
    assert targets == {'curriculum_subject', 'curriculum_cycle', 'curriculum_week', 'media_tag', 'topic', 'learning_entity'}
    assert topic.id in {link.to_entity for link in links}
    assert all(link.properties['source'] == 'media-classifier' for link in links)
    assert len([link for link in links if store.store.get_entity(link.to_entity).entity_type == 'curriculum_cycle']) == 3
    catalog = media.get_playlist_catalog()
    assert catalog['cc_tag_light'] and catalog['cc_topic_optics'] and catalog['cc_entity_reflection']
    old_ids = {link.id for link in links}
    store.sync_media_catalog()
    assert old_ids == {link.id for link in store.store.get_all_links() if link.relationship_type == 'classified_as'}
    # Conversation/user links must survive reclassification even when a topic changes.
    manual = store.store.create_link('about', entity.id, topic.id)
    manifest['tracks']['abcdefghijk']['title'] = 'Maps song'
    answer['tracks'][0].update(subjects=['geography'], topics=['cartography'], tags=['maps'], entities=[])
    await cc.enrich_manifest(folder, manifest, 'Sample')
    media.invalidate_playlist_cache(); store.sync_media_catalog()
    remaining = store.store.get_all_links()
    assert any(link.id == manual.id for link in remaining)
    assert not any(link.relationship_type == 'classified_as' and link.to_entity == topic.id for link in remaining)
    assert 'cc_topic_optics' not in media.get_playlist_catalog()
    store.store._db.close()


async def test_model_batches_are_paced(library, monkeypatch):
    from unittest.mock import AsyncMock
    _, _, _, _ = library
    sleeper = AsyncMock()
    monkeypatch.setattr(cc, 'MIN_BATCH_INTERVAL', 10)
    monkeypatch.setattr(cc, '_last_batch_start', 115)
    monkeypatch.setattr(cc.time, 'monotonic', lambda: 120)
    monkeypatch.setattr(cc.asyncio, 'sleep', sleeper)
    await cc.classify_tracks([{'video_id': 'abcdefghijk', 'title': 'Sample'}], 'Sample', {})
    sleeper.assert_awaited_once_with(5)


async def test_graph_sync_is_atomic_on_projection_failure(library, monkeypatch, tmp_path):
    folder, manifest, _, _ = library
    await cc.enrich_manifest(folder, manifest, 'Sample')
    monkeypatch.setattr(knowledge, 'ONTOLOGY_DB_PATH', tmp_path / 'ontology.db')
    store = KnowledgeStore(); store.connect()
    before = store.get_graph_snapshot()
    def fail(*args, **kwargs):
        raise RuntimeError('Sample graph failure')
    monkeypatch.setattr(store, 'sync_media_associations', fail)
    with pytest.raises(RuntimeError, match='Sample graph failure'):
        store.sync_media_catalog()
    assert store.get_graph_snapshot() == before
    store.store._db.close()
