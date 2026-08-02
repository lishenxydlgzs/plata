"""Knowledge graph for media catalog, conversation messages, and topic memory.

Graph structure:
  [message] --mentions--> [topic]
  [message] --triggered--> [media]
  [media]   --about--> [topic]

Messages carry timestamps, so topic frequency and recency are derived
from traversing edges rather than stored as counters.
"""

import logging
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ontology import OntologyStore, EntityFilter, initialize_database
from ontology.types import EntityType, LinkType

from .media import scan_media_catalog

logger = logging.getLogger(__name__)

DB_DIR = Path(os.environ.get("DB_DIR", "./data"))
ONTOLOGY_DB_PATH = DB_DIR / "ontology.db"

MAX_INTERESTS_IN_PROMPT = 8


class _TypeRegistry:
    _entity_types = [
        EntityType(id="media", name="Media", properties={}, system_defined=True, created_at="", updated_at="", description="A playable audio/video file"),
        EntityType(id="topic", name="Topic", properties={}, system_defined=True, created_at="", updated_at="", description="A subject or theme"),
        EntityType(id="message", name="Message", properties={}, system_defined=True, created_at="", updated_at="", description="A user message in a conversation"),
    ]
    _link_types = [
        LinkType(id="about", name="About", from_entity_type="media", to_entity_type="topic", bidirectional=False, created_at=""),
        LinkType(id="mentions", name="Mentions", from_entity_type="message", to_entity_type="topic", bidirectional=False, created_at=""),
        LinkType(id="triggered", name="Triggered", from_entity_type="message", to_entity_type="media", bidirectional=False, created_at=""),
    ]

    def get_entity_types(self) -> list[EntityType]:
        return self._entity_types

    def get_entity_type(self, id: str) -> EntityType | None:
        return next((t for t in self._entity_types if t.id == id), None)

    def get_link_types(self) -> list[LinkType]:
        return self._link_types

    def get_link_type(self, id: str) -> LinkType | None:
        return next((t for t in self._link_types if t.id == id), None)


class KnowledgeStore:
    def __init__(self) -> None:
        self._store: OntologyStore | None = None

    def connect(self) -> None:
        DB_DIR.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(str(ONTOLOGY_DB_PATH))
        initialize_database(db)
        self._store = OntologyStore(db, _TypeRegistry())
        logger.info("Knowledge store connected: %s", ONTOLOGY_DB_PATH)

    @property
    def store(self) -> OntologyStore:
        assert self._store is not None
        return self._store

    def sync_media_catalog(self) -> None:
        """Sync filesystem media catalog into the ontology store."""
        catalog = scan_media_catalog()
        for item in catalog:
            self.upsert_media(
                file_id=item["id"],
                title=item["title"],
                filename=item["file"],
                media_content_type=item.get("media_content_type", "music"),
            )
        logger.info("Synced %d media files into ontology", len(catalog))

    # ─── Media Catalog ────────────────────────────────────────────────────────

    def upsert_media(self, file_id: str, title: str, filename: str, **props: Any) -> str:
        """Create or update a media entity. Returns entity ID."""
        properties = {"filename": filename, "title": title, **props}
        entity, _ = self.store.upsert_entity("media", title, properties=properties, match_on=("media_file", file_id))
        return entity.id

    def link_media_to_topic(self, media_id: str, topic_name: str) -> None:
        """Link a media entity to a topic."""
        topic, _ = self.store.upsert_entity("topic", topic_name.strip().lower())
        self.store.upsert_link("about", media_id, topic.id)

    def get_media_by_topic(self, topic_name: str, limit: int = 5) -> list[dict[str, Any]]:
        """Find media related to a topic."""
        topic_entity = self.store.query_entities(EntityFilter(entity_type="topic", name_contains=topic_name.strip().lower(), limit=1))
        if not topic_entity:
            return []
        results = self.store.get_linked_entities_by_type([topic_entity[0].id], ["media"], limit=limit)
        return [r["entity"].properties for r in results]

    # ─── Messages & Topics ────────────────────────────────────────────────────

    def record_message(self, text: str, conversation_id: str, topics: list[str], media_id: str | None = None) -> None:
        """Record a user message and its extracted topics/media as graph nodes and edges.

        Creates:
          [message] --mentions--> [topic]  (for each topic)
          [message] --triggered--> [media] (if media was played)
          [media] --about--> [topic]       (links media to topics from this message)
        """
        now = datetime.now(timezone.utc).isoformat()
        msg_entity = self.store.create_entity(
            "message", text[:100],
            properties={"text": text, "conversation_id": conversation_id, "timestamp": now},
            created_at=now,
        )

        topic_ids: list[str] = []
        for topic in topics:
            topic_clean = topic.strip().lower()
            if not topic_clean or len(topic_clean) < 2:
                continue
            topic_entity, _ = self.store.upsert_entity("topic", topic_clean, properties={})
            self.store.create_link("mentions", msg_entity.id, topic_entity.id)
            topic_ids.append(topic_entity.id)
            logger.info("Message --mentions--> %r", topic_clean)

        if media_id:
            media_entity = self.store.get_entity_by_identifier("media_file", media_id)
            if media_entity:
                self.store.create_link("triggered", msg_entity.id, media_entity.id)
                logger.info("Message --triggered--> %r", media_entity.name)
                # Also link the media to topics from this message
                for tid in topic_ids:
                    self.store.upsert_link("about", media_entity.id, tid)

    # ─── Memory / Context Building ───────────────────────────────────────────

    def get_recent_interests(self, limit: int = MAX_INTERESTS_IN_PROMPT) -> list[dict[str, Any]]:
        """Get top topics scored by recency-weighted mention count.

        Uses a single SQL query with time-decay scoring:
        each mention contributes 1/(1 + age_in_days), so recent mentions
        score higher than old ones.
        """
        db = self.store._db
        rows = db.execute("""
            SELECT t.name,
                   COUNT(*) as mention_count,
                   MAX(l.created_at) as last_mentioned,
                   SUM(1.0 / (1.0 + julianday('now') - julianday(l.created_at))) as score
            FROM links l
            JOIN entities t ON t.id = l.to_entity
            WHERE l.relationship_type = 'mentions'
              AND t.entity_type = 'topic'
            GROUP BY t.id
            ORDER BY score DESC
            LIMIT ?
        """, (limit,)).fetchall()
        return [
            {"name": row["name"], "mention_count": row["mention_count"], "last_mentioned": row["last_mentioned"]}
            for row in rows
        ]

    def get_relevant_media_for_interests(self, limit: int = 3) -> list[dict[str, Any]]:
        """Find media that matches recent conversation topics via graph edges."""
        interests = self.get_recent_interests(limit=5)
        if not interests:
            return []
        topic_entities = []
        for interest in interests:
            found = self.store.query_entities(EntityFilter(entity_type="topic", name_contains=interest["name"], limit=1))
            topic_entities.extend(found)
        if not topic_entities:
            return []
        topic_ids = [e.id for e in topic_entities]
        results = self.store.get_linked_entities_by_type(topic_ids, ["media"], limit=limit)
        return [
            {
                "id": r["entity"].properties.get("filename", "").rsplit(".", 1)[0].lower().replace("-", "_").replace(" ", "_"),
                "title": r["entity"].properties.get("title", r["entity"].name),
                "file": r["entity"].properties.get("filename", ""),
            }
            for r in results
        ]

    def build_memory_prompt(self) -> str:
        """Build a context string about recent interests for the system prompt."""
        interests = self.get_recent_interests()
        if not interests:
            return ""
        top_topics = [i["name"] for i in interests[:6]]
        return f"Topics we've talked about recently: {', '.join(top_topics)}."
