import json

import pytest
from httpx import ASGITransport, AsyncClient

from agent_server.app import app, conversation_db, knowledge_store
from agent_server.household import GuidanceRequest, PersonRequest, MemorySettings, BehaviorReview
from agent_server.knowledge import KnowledgeStore
from agent_server.models import ConversationRequest
from agent_server.modes import chat


@pytest.fixture
def store():
    knowledge_store.connect()
    yield knowledge_store
    knowledge_store.store._db.close()


@pytest.fixture
async def client(store):
    await conversation_db.connect()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client
    await conversation_db.close()


def record(store, kind="learning_event", conversation="lesson", **changes):
    report = {"child_name": "Sample Child", "summary": "Practiced A to H",
              "topic": "alphabet", "material": "A to H", "outcome": "practiced",
              "reporter_claim": "Dad", **changes}
    message = store.record_message(text="Sample report", conversation_id=conversation, topics=[])
    return store.record_events(kind, [report], message, conversation, "Sample report")[0]


def test_guidance_revision_preserves_event_interpretation(store):
    first = store.save_guidance_document(GuidanceRequest(
        document_key="values", title="Household values", content="# Responsibility\nRepair harm."))
    event = record(store, "behavior_event", summary="Helped repair a broken item",
                   interpretations=[{"document_id": first["id"], "section": "Responsibility",
                                     "explanation": "Repair reflects responsibility."}])
    second = store.save_guidance_document(GuidanceRequest(
        document_key="values", title="Household values", content="# Responsibility\nKeep commitments."))
    assert second["version"] == 2
    assert [d["id"] for d in store.get_guidance_documents()] == [second["id"]]
    assert store.store.get_entity(first["id"]).properties["content"].endswith("Repair harm.")
    link = next(l for l in store.get_graph_snapshot()["links"]
                if l["type"] == "interpreted_using" and l["from"] == event["id"])
    assert link["to"] == first["id"] and link["properties"]["version"] == 1
    prompt = store.build_guidance_prompt()
    assert "Keep commitments." in prompt and "Repair harm." not in prompt


def test_learning_session_person_aliases_and_graph_links(store):
    person = store.create_person(PersonRequest(name="Sample Child", aliases=["Learner"]))
    start = record(store, child_name="Learner", outcome="started", material="alphabet")
    progress = record(store, outcome="recalled", material="H to K")
    assert start["person_id"] == progress["person_id"] == person["id"]
    assert progress["session_id"] == start["id"]
    assert progress["reporter_verification"] == "unverified"
    other = record(store, conversation="another", outcome="recalled", session_id=start["id"])
    assert other["session_id"] is None
    graph = store.get_graph_snapshot()
    assert {l["type"] for l in graph["links"] if l["from"] == progress["id"]} >= {"involves", "about"}
    assert any(l["type"] == "reports" and l["to"] == progress["id"] for l in graph["links"])


def test_ambiguous_name_does_not_merge_people(store):
    a = store.create_person(PersonRequest(name="Sample Child"))
    store.create_person(PersonRequest(name="Sample Child"))
    assert store.resolve_person("Sample Child") is None
    assert store.resolve_person("Sample Child", a["id"])["id"] == a["id"]
    assert store.resolve_person("Sample Child", "unknown") is None


def test_scoped_retrieval_and_no_global_incident_memory(store):
    event = record(store)
    record(store, child_name="Other Child", summary="Other child's lesson")
    record(store, topic="numbers", summary="Unrelated counting lesson")
    record(store, "behavior_event", summary="A private correction")
    memory = store.lookup_memory({"person_id": event["person_id"], "kind": "learning_event", "topic": "ALPHABET"})
    assert [e["id"] for e in memory["events"]] == [event["id"]]
    assert "source_text" not in memory["events"][0]
    assert "A private correction" not in store.build_memory_prompt()
    assert "error" in store.lookup_memory({"person_id": event["person_id"], "kind": "learning_event"})


def test_event_validation_limits_and_idempotence(store):
    message = store.record_message(text="Report", conversation_id="test", topics=[])
    report = {"child_name": "Sample Child", "summary": "Practiced", "topic": "alphabet",
              "material": "A to H", "outcome": "practiced"}
    save = lambda events: store.record_events("learning_event", events, message, "test", "Report")
    assert save({}) == []
    assert save([None, {**report, "outcome": "genius"}]) == []
    first = save([report, {**report, "material": "H to K"}, {**report, "material": "L to M"}])
    assert len(first) == 2
    assert save([report])[0]["id"] == first[0]["id"]
    assert len(store.get_events("learning_event")) == 2
    assert save([{**report, "child_name": None}]) == []


def test_independent_memory_switches_and_review_preserve_report(store):
    store.save_guidance_document(GuidanceRequest(document_key="values", title="Values", content="Be kind."))
    event = record(store, "behavior_event", summary="Broke an item")
    reviewed = store.review_behavior_event(event["id"], BehaviorReview(status="repaired", note="Helped fix it."))
    assert reviewed["summary"] == "Broke an item"
    assert reviewed["reviews"][0]["note"] == "Helped fix it."
    store.save_memory_settings(MemorySettings(behavior_logging=False, learning_logging=True))
    message = store.record_message(text="Report", conversation_id="test", topics=[])
    assert store.record_events("behavior_event", [{"child_name": "Sample Child", "summary": "Helped"}], message, "test", "Report") == []
    assert record(store)["outcome"] == "practiced"
    assert "Be kind." in store.build_guidance_prompt()
    assert store.lookup_memory({"person_id": event["person_id"], "kind": "behavior_event"})["events"] == []


async def test_alphabet_history_is_retrieved_before_encouragement(store, monkeypatch):
    prior = record(store, occurred_at_description="yesterday")
    record(store, child_name="Other Child", summary="Must not appear in retrieved context")
    calls = []

    async def generate(prompt, history, text):
        calls.append(prompt)
        assert text == "I am Dad. Sample Child remembers H to K today."
        if len(calls) == 1:
            assert "Practiced A to H" not in prompt
            return {"memory_query": {"person_id": prior["person_id"], "kind": "learning_event", "topic": "alphabet"}}
        retrieved = prompt.split("Retrieved household memory:\n")[1].split("\nLookup is complete.")[0]
        assert json.loads(retrieved)["events"][0]["material"] == "A to H"
        assert "Must not appear" not in retrieved
        return {"reply_text": "Great job! You practiced A to H, and now you remember H to K!",
                "learning_events": [{"person_id": prior["person_id"], "child_name": "Sample Child",
                                     "summary": "Recalled H to K", "topic": "alphabet", "material": "H to K",
                                     "outcome": "recalled", "reporter_claim": "Dad"}]}

    monkeypatch.setattr(chat, "generate_chat_json", generate)
    reply = await chat.ChatHandler(store).handle(ConversationRequest(
        text="I am Dad. Sample Child remembers H to K today.", conversation_id="today"), [])
    assert reply.reply_text.startswith("Great job!")
    assert len(calls) == 2
    events = store.get_events("learning_event", prior["person_id"], "alphabet")
    assert [e["outcome"] for e in events] == ["recalled", "practiced"]


async def test_lookup_failure_saves_no_draft_events(store, monkeypatch):
    prior = record(store)
    calls = 0

    async def generate(prompt, history, text):
        nonlocal calls
        calls += 1
        if calls == 1:
            return {"reply_text": "Draft", "memory_query": {"person_id": prior["person_id"], "kind": "learning_event", "topic": "alphabet"},
                    "learning_events": [{"child_name": "Sample Child", "summary": "Should not save", "topic": "alphabet", "material": "A to Z", "outcome": "mastered"}]}
        raise RuntimeError("LLM unavailable")

    monkeypatch.setattr(chat, "generate_chat_json", generate)
    response = await chat.ChatHandler(store).handle(ConversationRequest(text="Progress?", conversation_id="test"), [])
    assert response.reply_text == chat.FALLBACK_REPLY
    assert len(store.get_events("learning_event")) == 1


async def test_conversation_history_is_scoped_and_ordered(client):
    await conversation_db.save_turn("first", "First question", "First reply")
    await conversation_db.save_turn("second", "Other child", "Other reply")
    await conversation_db.save_turn("first", "Follow-up", "Answer")
    assert [m["text"] for m in await conversation_db.get_history("first")] == [
        "First question", "First reply", "Follow-up", "Answer"]


async def test_household_apis(client):
    response = await client.post("/api/guidance-documents", json={"document_key": "values", "title": "Values", "content": "# Kindness\nBe kind."})
    assert response.status_code == 200
    assert (await client.get("/api/guidance-documents")).json()[0]["content"].startswith("# Kindness")
    assert (await client.post("/api/guidance-documents", json={"document_key": "values", "title": "Values", "content": "  "})).status_code == 422
    person = (await client.post("/api/people", json={"name": "Sample Child", "aliases": ["Learner"]})).json()
    assert (await client.get("/api/people")).json()[0]["id"] == person["id"]
    assert (await client.get("/api/learning-events", params={"person_id": person["id"]})).json() == []
    assert (await client.patch("/api/behavior-events/missing", json={"status": "closed", "note": "Reviewed"})).status_code == 404
    assert (await client.put("/api/memory-settings", json={"behavior_logging": False, "learning_logging": True})).status_code == 200
    assert (await client.get("/api/memory-settings")).json()["behavior_logging"] is False


def test_document_deactivation_preserves_original_markdown(store):
    content = "\n# Guidance\n\n    Indented example\n"
    doc = store.save_guidance_document(GuidanceRequest(document_key="guide", title="Guide", content=content))
    assert doc["content"] == content
    store.save_guidance_document(GuidanceRequest(document_key="guide", title="Guide", content=content, active=False))
    assert store.get_guidance_documents() == []
    assert len(store.get_guidance_documents(include_history=True)) == 2


def test_learning_disabled_and_mismatched_person_id(store):
    event = record(store)
    assert store.resolve_person("Different Child", event["person_id"]) is None
    store.save_memory_settings(MemorySettings(behavior_logging=True, learning_logging=False))
    message = store.record_message(text="Lesson", conversation_id="test", topics=[])
    assert store.record_events("learning_event", [{"child_name": "Sample Child", "summary": "Practiced",
           "topic": "alphabet", "material": "A to H", "outcome": "practiced"}], message, "test", "Lesson") == []
    assert store.lookup_memory({"person_id": event["person_id"], "kind": "learning_event", "topic": "alphabet"})["events"] == []
    assert record(store, "behavior_event")["id"]


async def test_lesson_start_then_pronoun_progress_over_api(client, store, monkeypatch):
    first_text = "I am Dad, starting to teach Sample Child alphabet."
    followup = "She remembers H to K now."
    calls = []

    async def generate(prompt, history, text):
        calls.append(text)
        if text == first_text:
            return {"reply_text": "Have fun learning your letters, Sample Child!",
                    "learning_events": [{"child_name": "Sample Child", "summary": "Started alphabet lesson",
                                         "topic": "alphabet", "material": "alphabet", "outcome": "started", "reporter_claim": "Dad"}]}
        assert first_text in [m["text"] for m in history]
        person = store.get_people()[0]
        if "Retrieved household memory:" not in prompt:
            return {"memory_query": {"person_id": person["id"], "kind": "learning_event", "topic": "alphabet"}}
        assert '"same_conversation": true' in prompt
        return {"reply_text": "Great job remembering H to K, Sample Child!",
                "learning_events": [{"person_id": person["id"], "child_name": "Sample Child", "summary": "Recalled H to K",
                                     "topic": "alphabet", "material": "H to K", "outcome": "recalled", "reporter_claim": "Dad"}]}

    monkeypatch.setattr(chat, "generate_chat_json", generate)
    assert (await client.post("/conversation", json={"text": first_text, "conversation_id": "lesson"})).status_code == 200
    response = await client.post("/conversation", json={"text": followup, "conversation_id": "lesson"})
    assert response.json()["reply_text"] == "Great job remembering H to K, Sample Child!"
    events = (await client.get("/api/learning-events")).json()
    assert len(events) == 2
    assert events[0]["session_id"] == events[1]["id"]
    assert len(calls) == 3


async def test_invalid_lookup_cannot_save_events(client, store, monkeypatch):
    calls = 0

    async def generate(prompt, history, text):
        nonlocal calls
        calls += 1
        if calls == 1:
            return {"memory_query": {"person_id": "nonexistent", "kind": "learning_event", "topic": "alphabet"}}
        return {"reply_text": "Which child do you mean?", "behavior_events": [{"child_name": "Guessed Child", "summary": "Guessed event"}]}

    monkeypatch.setattr(chat, "generate_chat_json", generate)
    response = await client.post("/conversation", json={"text": "She did well", "conversation_id": "unknown"})
    assert response.json()["reply_text"] == "Which child do you mean?"
    assert store.get_events("behavior_event") == []
    assert store.get_people() == []
