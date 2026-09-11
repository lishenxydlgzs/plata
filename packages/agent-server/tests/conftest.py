"""All backend tests use disposable databases, never operational household data."""

import pytest

from agent_server import context, knowledge


@pytest.fixture(autouse=True)
def isolated_databases(tmp_path, monkeypatch):
    monkeypatch.setattr(context, "DB_DIR", tmp_path)
    monkeypatch.setattr(context, "DB_PATH", tmp_path / "conversations.db")
    monkeypatch.setattr(knowledge, "DB_DIR", tmp_path)
    monkeypatch.setattr(knowledge, "ONTOLOGY_DB_PATH", tmp_path / "ontology.db")
