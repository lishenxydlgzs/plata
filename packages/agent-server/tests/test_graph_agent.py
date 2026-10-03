import pytest
from ag_ui.core import EventType, RunAgentInput, UserMessage
from agent_server.context import ConversationDB
from agent_server.knowledge import KnowledgeStore
from agent_server.maintenance import MaintenanceJob
from agent_server.graph_review import GraphReviewService
from agent_server.graph_agent import GraphBrowserReview
from agent_server.logbook_chat import ChatRequest
from .test_agent_stream import ScriptedModel


@pytest.fixture
async def graph():
    knowledge = KnowledgeStore(); knowledge.connect()
    conversations = ConversationDB(); await conversations.connect()
    review = GraphReviewService(knowledge, conversations, MaintenanceJob(knowledge))
    yield GraphBrowserReview(review)
    knowledge.store._db.close(); await conversations.close()


async def run_input(graph, text):
    session = await graph.service._conversations.create_graph_review_session()
    request = ChatRequest(text=text, request_id='request-one')
    body = RunAgentInput(thread_id=session['id'], run_id='run-one', messages=[UserMessage(id=request.request_id, content=text)],
                         state={}, tools=[], context=[], forwarded_props={})
    return body, request, await graph.prepare(session['id'], request)


async def test_graph_correction_is_atomic_replayable_and_preserves_sources(graph):
    store = graph.service._knowledge
    id = store.record_message('Original sample transcription', 'sample', [])
    body, request, prepared = await run_input(graph, f'Correct record {id} to Corrected sample transcription')
    model = ScriptedModel([{'name': 'stage_graph_changes', 'input': {'changes': [
        {'type': 'correct_record', 'id': id, 'corrected_text': 'Corrected sample transcription'}]}}, 'The transcription is corrected.'])
    events = [e async for e in graph.events(body, request, prepared, model=model)]
    assert events[-1].type == EventType.RUN_FINISHED
    props = store.store.get_entity(id).properties
    assert props['text'] == 'Original sample transcription'
    assert props['corrected_text'] == 'Corrected sample transcription'
    assert len(props['corrections']) == 1
    # A fresh service after restart returns the durable receipt, not another run.
    restarted = GraphBrowserReview(graph.service)
    receipt = await restarted.prepare(body.thread_id, request)
    assert receipt['complete']
    assert receipt['session']['pending'] is None
    assert len(receipt['session']['messages']) == 2
    assert receipt['session']['actions'][0]['applied']
    assert len(await restarted.sessions()) == 1


async def test_graph_batch_failure_rolls_back_earlier_mutation(graph):
    store = graph.service._knowledge.store
    fact = store.create_entity('fact', 'Original fact')
    message = graph.service._knowledge.record_message('Original message', 'sample', [])
    person = store.create_entity('person', 'Sample Person')
    body, request, prepared = await run_input(graph, f'Correct {fact.id} and {message}')
    model = ScriptedModel([{'name': 'stage_graph_changes', 'input': {'changes': [
        {'type': 'update', 'id': fact.id, 'new_name': 'Changed fact'},
        {'type': 'correct_record', 'id': message, 'corrected_text': 'Changed text', 'person_id': person.id},
    ]}}, 'Changes are ready.'])
    with pytest.raises(ValueError, match='rejected'):
        _ = [e async for e in graph.events(body, request, prepared, model=model)]
    assert store.get_entity(fact.id).name == 'Original fact'
    assert 'corrected_text' not in store.get_entity(message).properties
    assert (await graph.session(body.thread_id))['pending']
    assert not (await graph.prepare(body.thread_id, request))['complete']


async def test_failed_generation_keeps_pending_without_actions(graph):
    body, request, prepared = await run_input(graph, 'Review the sample records.')
    with pytest.raises(ValueError):
        _ = [e async for e in graph.events(body, request, prepared, model=ScriptedModel([RuntimeError('provider down')]))]
    assert (await graph.session(body.thread_id))['pending']['text'] == request.text
    with pytest.raises(ValueError, match='Retry'):
        await graph.prepare(body.thread_id, ChatRequest(text='Another message', request_id='next'))


async def test_graph_merge_preserves_evidence_and_rejects_self_merge(graph):
    store = graph.service._knowledge.store
    keep = store.create_entity('fact', 'Sample fact')
    remove = store.create_entity('fact', 'Duplicate sample fact')
    source = graph.service._knowledge.record_message('Sample source', 'sample', [])
    store.create_link('supports', source, remove.id)
    body, request, prepared = await run_input(graph, f'Merge duplicate {remove.id} into {keep.id}')
    model = ScriptedModel([{'name': 'stage_graph_changes', 'input': {'changes': [
        {'type': 'merge', 'keep_id': keep.id, 'remove_id': remove.id}]}}, 'The duplicate is merged.'])
    events = [e async for e in graph.events(body, request, prepared, model=model)]
    assert events[-1].type == EventType.RUN_FINISHED
    assert store.get_entity(remove.id) is None
    assert any(l.from_entity == source for l in store.get_entity_links(keep.id))
