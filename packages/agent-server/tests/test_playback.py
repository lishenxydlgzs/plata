"""Tests for ontology-backed resumable playlist playback."""

from pathlib import Path

import pytest

from agent_server import knowledge, media
from agent_server.knowledge import KnowledgeStore


@pytest.fixture
def playback_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> KnowledgeStore:
    media_dir = tmp_path / "media"
    week3 = media_dir / "cc_cycle3" / "week_3"
    week5 = media_dir / "cc_cycle3" / "week_5"
    week3.mkdir(parents=True)
    week5.mkdir(parents=True)
    (week3 / "01_Bible.mp3").write_bytes(b"fake")
    (week3 / "02_History.mp3").write_bytes(b"fake")
    (week3 / "03_Science.mp3").write_bytes(b"fake")
    (week5 / "01_English.mp3").write_bytes(b"fake")
    (week5 / "02_Math.mp3").write_bytes(b"fake")

    monkeypatch.setattr(media, "MEDIA_DIR", media_dir)
    monkeypatch.setattr(knowledge, "ONTOLOGY_DB_PATH", tmp_path / "ontology.db")
    media._playlist_cache = None

    store = KnowledgeStore()
    store.connect()
    store.sync_media_catalog()
    return store


def test_resume_starts_after_last_completed_track(playback_store: KnowledgeStore):
    started = playback_store.begin_playback("cc_cycle3_week_3", 3, "play")
    playback_store.update_playback(started["session_id"], "track_completed", 0)
    playback_store.update_playback(started["session_id"], "interrupted")

    resumed = playback_store.begin_playback("cc_cycle3_week_3", 3, "resume")

    assert resumed == {"session_id": started["session_id"], "start_index": 1}


def test_starting_another_week_preserves_previous_cursor(
    playback_store: KnowledgeStore,
):
    week3 = playback_store.begin_playback("cc_cycle3_week_3", 3, "play")
    playback_store.update_playback(week3["session_id"], "track_completed", 0)

    week5 = playback_store.begin_playback("cc_cycle3_week_5", 2, "play")

    week3_entity = playback_store.store.get_entity(week3["session_id"])
    week5_entity = playback_store.store.get_entity(week5["session_id"])
    assert week3_entity is not None
    assert week3_entity.properties["status"] == "interrupted"
    assert week3_entity.properties["next_track_index"] == 1
    assert week5_entity is not None
    assert week5_entity.properties["status"] == "playing"


def test_final_track_marks_session_complete(playback_store: KnowledgeStore):
    started = playback_store.begin_playback("cc_cycle3_week_5", 2, "play")

    playback_store.update_playback(started["session_id"], "track_completed", 0)
    state = playback_store.update_playback(
        started["session_id"], "track_completed", 1
    )

    assert state["next_track_index"] == 2
    assert state["status"] == "completed"
    assert state["completed_at"]


def test_playback_prompt_includes_next_track(playback_store: KnowledgeStore):
    started = playback_store.begin_playback("cc_cycle3_week_3", 3, "play")
    playback_store.update_playback(started["session_id"], "track_completed", 0)
    playback_store.update_playback(started["session_id"], "stopped")

    prompt = playback_store.build_playback_prompt()

    assert "cc_cycle3_week_3: 1 of 3 completed" in prompt
    assert "next: 02 History" in prompt
    assert "status: stopped" in prompt


def test_resume_unstarted_week_begins_at_zero(playback_store: KnowledgeStore):
    resumed = playback_store.begin_playback("cc_cycle3_week_5", 2, "resume")

    assert resumed["start_index"] == 0


def test_late_completion_does_not_advance_stopped_session(
    playback_store: KnowledgeStore,
):
    started = playback_store.begin_playback("cc_cycle3_week_3", 3, "play")
    playback_store.update_playback(started["session_id"], "stopped")

    state = playback_store.update_playback(
        started["session_id"], "track_completed", 0
    )

    assert state["status"] == "stopped"
    assert state["next_track_index"] == 0


def test_starting_same_week_supersedes_older_attempt(
    playback_store: KnowledgeStore,
):
    first = playback_store.begin_playback("cc_cycle3_week_3", 3, "play")
    playback_store.update_playback(first["session_id"], "track_completed", 0)
    second = playback_store.begin_playback("cc_cycle3_week_3", 3, "restart")

    first_entity = playback_store.store.get_entity(first["session_id"])
    assert first_entity is not None
    assert first_entity.properties["status"] == "superseded"
    assert second["session_id"] != first["session_id"]
    assert second["start_index"] == 0

    # A late callback from the cancelled attempt cannot make it resumable again.
    state = playback_store.update_playback(first["session_id"], "interrupted")
    assert state["status"] == "superseded"
