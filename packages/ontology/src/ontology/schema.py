"""SQLite schema definitions and database initialization."""

from __future__ import annotations

import sqlite3

ONTOLOGY_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS entities (
  id           TEXT PRIMARY KEY,
  entity_type  TEXT NOT NULL,
  name         TEXT NOT NULL,
  properties   TEXT NOT NULL,
  summary      TEXT,
  created_at   TEXT NOT NULL,
  updated_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_entities_type ON entities(entity_type);
CREATE INDEX IF NOT EXISTS idx_entities_name ON entities(name COLLATE NOCASE);

CREATE TABLE IF NOT EXISTS links (
  id                TEXT PRIMARY KEY,
  relationship_type TEXT NOT NULL,
  from_entity       TEXT NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
  to_entity         TEXT NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
  properties        TEXT,
  created_at        TEXT NOT NULL,
  UNIQUE(relationship_type, from_entity, to_entity)
);
CREATE INDEX IF NOT EXISTS idx_links_from ON links(from_entity);
CREATE INDEX IF NOT EXISTS idx_links_to ON links(to_entity);
CREATE INDEX IF NOT EXISTS idx_links_type ON links(relationship_type);

CREATE TABLE IF NOT EXISTS ontology_settings (
  key        TEXT PRIMARY KEY,
  value      TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS entity_identifiers (
  entity_id    TEXT NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
  system       TEXT NOT NULL,
  external_id  TEXT NOT NULL,
  created_at   TEXT NOT NULL,
  UNIQUE(system, external_id)
);
CREATE INDEX IF NOT EXISTS idx_entity_identifiers_entity ON entity_identifiers(entity_id);
CREATE INDEX IF NOT EXISTS idx_entity_identifiers_lookup ON entity_identifiers(system, external_id);
"""

ONTOLOGY_FTS5_SQL = """
CREATE VIRTUAL TABLE IF NOT EXISTS entities_fts USING fts5(
  id UNINDEXED,
  name,
  summary,
  entity_type UNINDEXED,
  content='entities',
  content_rowid='rowid'
);
"""

ONTOLOGY_FTS5_TRIGGERS_SQL = """
DROP TRIGGER IF EXISTS entities_fts_ai;
CREATE TRIGGER entities_fts_ai AFTER INSERT ON entities BEGIN
  INSERT INTO entities_fts(rowid, id, name, summary, entity_type)
    VALUES (new.rowid, new.id, new.name, new.summary, new.entity_type);
END;

DROP TRIGGER IF EXISTS entities_fts_au;
CREATE TRIGGER entities_fts_au AFTER UPDATE ON entities BEGIN
  INSERT INTO entities_fts(entities_fts, rowid, id, name, summary, entity_type)
    VALUES ('delete', old.rowid, old.id, old.name, old.summary, old.entity_type);
  INSERT INTO entities_fts(rowid, id, name, summary, entity_type)
    VALUES (new.rowid, new.id, new.name, new.summary, new.entity_type);
END;

DROP TRIGGER IF EXISTS entities_fts_ad;
CREATE TRIGGER entities_fts_ad AFTER DELETE ON entities BEGIN
  INSERT INTO entities_fts(entities_fts, rowid, id, name, summary, entity_type)
    VALUES ('delete', old.rowid, old.id, old.name, old.summary, old.entity_type);
END;
"""


def initialize_database(db: sqlite3.Connection, *, enable_fts: bool = True) -> None:
    db.execute("PRAGMA journal_mode = WAL")
    db.execute("PRAGMA foreign_keys = ON")
    db.executescript(ONTOLOGY_SCHEMA_SQL)
    if enable_fts:
        try:
            db.executescript(ONTOLOGY_FTS5_SQL)
            db.executescript(ONTOLOGY_FTS5_TRIGGERS_SQL)
        except sqlite3.OperationalError:
            pass
