"""Classification validates batches and persists retry/cache state in the ontology."""
import json
from copy import deepcopy

import pytest
from agent_server import cc_catalog as cc, media
from agent_server.knowledge import KnowledgeStore
from .catalog_helpers import catalog_view


def result(video_id='abcdefghijk'):
    return dict(video_id=video_id, subjects=['science'], cycles=[], all_cycles=True,
                weeks=[3], tags=['light'], topics=['optics'],
                entities=[{'kind': 'concept', 'name': 'Reflection'}],
                confidence=0.85, needs_review=False, explanation='The title describes light.')


@pytest.fixture
def library(tmp_path, monkeypatch):
    folder = tmp_path / 'media' / 'sample'
    folder.mkdir(parents=True)
    (folder / 'sample__abcdefghijk.mp3').write_bytes(b'audio')
    monkeypatch.setattr(media, 'MEDIA_DIR', folder.parent)
    store = KnowledgeStore(); store.connect(); store.sync_media_catalog()
    store.media_catalog.configure_source('sample', cc_mode=True)
    store.media_catalog.publish('sample', 'abcdefghijk', 'A song about light', 'sample__abcdefghijk.mp3', 0)
    calls = []
    answer = {'tracks': [result()]}

    async def generate(prompt, history, text, **kwargs):
        assert 'untrusted source data' in prompt
        calls.append(json.loads(text))
        return deepcopy(answer)

    monkeypatch.setattr(cc, 'generate_chat_json', generate)
    monkeypatch.setattr(cc, 'MIN_BATCH_INTERVAL', 0)
    yield store, folder, calls, answer
    store.store._db.close()


async def enrich(store, folder, title='Sample'):
    return await cc.enrich_catalog(folder, store.media_catalog.source_state('sample'), title,
                                   store.media_catalog.save_classifications)


def track(store):
    return store.media_catalog.source_state('sample')['tracks']['abcdefghijk']


async def test_cache_and_title_context_changes_persist_without_manifest(library):
    store, folder, calls, answer = library
    assert await enrich(store, folder) == []
    song = track(store)
    assert song['subjects'] == ['science']
    assert song['classification']['source_title'] == 'A song about light'
    assert await enrich(store, folder) == []
    assert len(calls) == 1
    store.media_catalog.publish('sample', 'abcdefghijk', 'A song about maps', song['filename'], 0)
    answer['tracks'][0].update(subjects=['geography'], tags=['maps'], topics=['cartography'])
    assert await enrich(store, folder) == []
    assert track(store)['subjects'] == ['geography']
    assert len(calls) == 2
    await enrich(store, folder, 'Changed playlist context')
    assert len(calls) == 3
    assert not (folder / cc.MANIFEST).exists()
    assert (folder / song['filename']).read_bytes() == b'audio'


@pytest.mark.parametrize('mutation', ['missing', 'extra', 'duplicate', 'bad-week', 'bad-scope', 'unknown-subject', 'bad-entity'])
async def test_invalid_batch_keeps_metadata_and_retries_after_restart(library, mutation):
    store, folder, calls, answer = library
    await enrich(store, folder)
    previous = track(store)
    store.media_catalog.publish('sample', 'abcdefghijk', 'Updated title', previous['filename'], 0)
    good = deepcopy(answer)
    if mutation == 'missing': answer['tracks'] = []
    elif mutation == 'extra': answer['tracks'].append(result('lmnopqrstuv'))
    elif mutation == 'duplicate': answer['tracks'].append(deepcopy(answer['tracks'][0]))
    elif mutation == 'bad-week': answer['tracks'][0]['weeks'] = [25]
    elif mutation == 'bad-scope': answer['tracks'][0]['cycles'] = [3]
    elif mutation == 'unknown-subject': answer['tracks'][0]['subjects'] = ['invented-subject']
    elif mutation == 'bad-entity': answer['tracks'][0]['entities'] = [{'kind': 'household_person', 'name': 'Sample'}]
    assert await enrich(store, folder)
    failed = track(store)
    assert failed['classification_status'] == 'error'
    assert failed['classification'] == previous['classification']
    assert failed['subjects'] == previous['subjects']
    restarted = KnowledgeStore(); restarted.connect(); restarted.sync_media_catalog()
    answer.clear(); answer.update(good)
    assert await enrich(restarted, folder) == []
    assert track(restarted)['classification_status'] == 'ready'
    assert 'classification_error' not in track(restarted)
    assert len(calls) == 3
    restarted.store._db.close()


async def test_provider_failure_keeps_unclassified_song_discoverable(library, monkeypatch):
    store, folder, _, _ = library
    async def fail(*args, **kwargs):
        raise RuntimeError('Sample provider unavailable')
    monkeypatch.setattr(cc, 'generate_chat_json', fail)
    assert await enrich(store, folder)
    song = track(store)
    assert song['subjects'] == [] and song['classification_status'] == 'error'
    assert catalog_view(store)['cc_unclassified'][0]['id'] == song['id']


async def test_batches_and_graph_associations_reconcile_owned_links(library, monkeypatch):
    store, folder, calls, answer = library
    topic = store.store.create_entity('topic', 'optics')
    await enrich(store, folder)
    song = track(store)
    old = [l for l in store.store.get_all_links() if l.from_entity == song['id'] and l.relationship_type == 'classified_as']
    kinds = {store.store.get_entity(l.to_entity).entity_type for l in old}
    assert kinds == {'curriculum_subject', 'curriculum_cycle', 'curriculum_week', 'media_tag', 'topic', 'learning_entity'}
    assert topic.id in {l.to_entity for l in old}
    manual = store.store.create_link('about', song['id'], topic.id)
    store.media_catalog.publish('sample', 'abcdefghijk', 'Maps song', song['filename'], 0)
    answer['tracks'][0].update(subjects=['geography'], topics=['cartography'], tags=['maps'], entities=[])
    await enrich(store, folder)
    links = store.store.get_all_links()
    assert any(l.id == manual.id for l in links)
    assert not any(l.from_entity == song['id'] and l.relationship_type == 'classified_as' and l.to_entity == topic.id for l in links)
    assert 'cc_topic_optics' not in catalog_view(store)
    # More than one batch; previously classified songs stay cached.
    for index in range(9):
        video_id = f'{index:011d}'
        (folder / (video_id + '.mp3')).write_bytes(b'audio')
        store.media_catalog.publish('sample', video_id, 'Sample title', video_id + '.mp3', index + 1)
    batches = []
    async def classify(prompt, history, text, **kwargs):
        inputs = json.loads(text)['tracks']; batches.append(inputs)
        return {'tracks': [result(item['video_id']) for item in inputs]}
    monkeypatch.setattr(cc, 'generate_chat_json', classify)
    assert await enrich(store, folder) == []
    assert [len(b) for b in batches] == [8, 1]
    assert await enrich(store, folder) == []
    assert len(batches) == 2


async def test_model_batches_are_paced(library, monkeypatch):
    from unittest.mock import AsyncMock
    sleeper = AsyncMock()
    monkeypatch.setattr(cc, 'MIN_BATCH_INTERVAL', 10)
    monkeypatch.setattr(cc, '_last_batch_start', 115)
    monkeypatch.setattr(cc.time, 'monotonic', lambda: 120)
    monkeypatch.setattr(cc.asyncio, 'sleep', sleeper)
    await cc.classify_tracks([{'video_id': 'abcdefghijk', 'title': 'Sample'}], 'Sample', {})
    sleeper.assert_awaited_once_with(5)


async def test_interrupted_model_call_leaves_durable_pending_state(library, monkeypatch):
    import asyncio
    store, folder, calls, answer = library
    original = cc.generate_chat_json
    async def cancel(*args, **kwargs):
        raise asyncio.CancelledError()
    monkeypatch.setattr(cc, 'generate_chat_json', cancel)
    with pytest.raises(asyncio.CancelledError):
        await enrich(store, folder)
    assert track(store)['classification_status'] == 'pending'
    monkeypatch.setattr(cc, 'generate_chat_json', original)
    assert await enrich(store, folder) == []
    assert track(store)['classification_status'] == 'ready'


async def test_inflight_result_does_not_overwrite_new_title(library, monkeypatch):
    store, folder, _, answer = library
    async def edit_during_call(*args, **kwargs):
        song = store.store.get_entity(track(store)['id'])
        store.media_catalog.save(song, name='New title during classification')
        return deepcopy(answer)
    monkeypatch.setattr(cc, 'generate_chat_json', edit_during_call)
    await enrich(store, folder)
    song = track(store)
    assert song['title'] == 'New title during classification'
    assert song['classification_status'] == 'pending'
    assert not song['subjects']


async def test_error_with_matching_fingerprint_still_retries(library):
    store, folder, calls, _ = library
    await enrich(store, folder)
    entity = store.store.get_entity(track(store)['id'])
    store.media_catalog.save(entity, classification_status='error', classification_error='Sample transient error')
    await enrich(store, folder)
    assert len(calls) == 2
    assert track(store)['classification_status'] == 'ready'
