"""Knowledge graph for media catalog, conversation messages, topic memory, and facts.

Graph structure:
  [message] --mentions--> [topic]
  [message] --triggered--> [media]
  [message] --supports--> [fact]
  [media]   --about--> [topic]

Messages carry timestamps, so topic frequency and recency are derived
from traversing edges rather than stored as counters. Facts accumulate
confidence from multiple supporting messages.
"""

import json
import logging
import os
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ontology import OntologyStore, EntityFilter, initialize_database
from ontology.types import EntityType, LinkType

from .media import scan_media_catalog, scan_playlist_catalog
from .household import HouseholdMemory

logger = logging.getLogger(__name__)

DB_DIR = Path(os.environ.get("DB_DIR", "./data"))
ONTOLOGY_DB_PATH = DB_DIR / "ontology.db"

MAX_INTERESTS_IN_PROMPT = 8


FACT_CONFIDENCE_THRESHOLD = 0.7
MAX_FACTS_IN_PROMPT = 6
MAX_FAMILY_VALUES_IN_PROMPT = 8
MAX_KID_EVENTS_IN_PROMPT = 5
MAX_KID_EVENTS_RETURNED = 50

VALID_KID_EVENT_TYPES = {"encouragement", "correction", "observation"}


class _TypeRegistry:
    _entity_types = [
        *[EntityType(id=kind, name=kind.replace("_", " ").title(), properties={},
                     system_defined=True, created_at="", updated_at="")
          for kind in ("guidance_document", "person", "learning_event", "behavior_event")],
        EntityType(id="media", name="Media", properties={}, system_defined=True, created_at="", updated_at="", description="A playable audio/video file"),
        EntityType(id="topic", name="Topic", properties={}, system_defined=True, created_at="", updated_at="", description="A subject or theme"),
        EntityType(id="message", name="Message", properties={}, system_defined=True, created_at="", updated_at="", description="A user message in a conversation"),
        EntityType(id="fact", name="Fact", properties={}, system_defined=True, created_at="", updated_at="", description="A factual statement about the family"),
        EntityType(id="family_value", name="Family Value", properties={}, system_defined=True, created_at="", updated_at="", description="A guiding family value used for encouragement and correction"),
        EntityType(id="kid_event", name="Kid Event", properties={}, system_defined=True, created_at="", updated_at="", description="A parent-described child behavior moment for encouragement, correction, reward, or redemption"),
        EntityType(id="playlist", name="Playlist", properties={}, system_defined=True, created_at="", updated_at="", description="An ordered collection of playable media"),
        EntityType(id="playback_session", name="Playback Session", properties={}, system_defined=True, created_at="", updated_at="", description="Persistent progress through a playlist"),
    ]
    _link_types = [
        LinkType(id="involves", name="Involves", from_entity_type="*", to_entity_type="person", bidirectional=False, created_at=""),
        LinkType(id="interpreted_using", name="Interpreted using", from_entity_type="*", to_entity_type="guidance_document", bidirectional=False, created_at=""),
        LinkType(id="about", name="About", from_entity_type="*", to_entity_type="topic", bidirectional=False, created_at=""),
        LinkType(id="mentions", name="Mentions", from_entity_type="message", to_entity_type="topic", bidirectional=False, created_at=""),
        LinkType(id="triggered", name="Triggered", from_entity_type="message", to_entity_type="media", bidirectional=False, created_at=""),
        LinkType(id="supports", name="Supports", from_entity_type="message", to_entity_type="fact", bidirectional=False, created_at=""),
        LinkType(id="reports", name="Reports", from_entity_type="message", to_entity_type="*", bidirectional=False, created_at=""),
        LinkType(id="reflects", name="Reflects", from_entity_type="kid_event", to_entity_type="family_value", bidirectional=False, created_at=""),
        LinkType(id="contains", name="Contains", from_entity_type="playlist", to_entity_type="media", bidirectional=False, created_at=""),
        LinkType(id="uses", name="Uses", from_entity_type="playback_session", to_entity_type="playlist", bidirectional=False, created_at=""),
        LinkType(id="started", name="Started", from_entity_type="message", to_entity_type="playback_session", bidirectional=False, created_at=""),
    ]

    def get_entity_types(self) -> list[EntityType]:
        return self._entity_types

    def get_entity_type(self, id: str) -> EntityType | None:
        return next((t for t in self._entity_types if t.id == id), None)

    def get_link_types(self) -> list[LinkType]:
        return self._link_types

    def get_link_type(self, id: str) -> LinkType | None:
        return next((t for t in self._link_types if t.id == id), None)


class KnowledgeStore(HouseholdMemory):
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

        playlist_catalog = scan_playlist_catalog()
        nested_count = 0
        for playlist_id, tracks in playlist_catalog.items():
            playlist, _ = self.store.upsert_entity(
                "playlist",
                playlist_id.replace("_", " ").title(),
                properties={"playlist_id": playlist_id, "track_count": len(tracks)},
                match_on=("playlist_id", playlist_id),
            )
            for position, track in enumerate(tracks):
                track_id = track["file"].lower().replace("-", "_").replace(" ", "_")
                media_id = self.upsert_media(
                    file_id=track_id,
                    title=track["title"],
                    filename=track["file"],
                    media_content_type=track.get("media_content_type", "music"),
                    playlist_id=playlist_id,
                )
                self.store.upsert_link("contains", playlist.id, media_id, {"position": position})
                nested_count += 1
        logger.info(
            "Synced %d root media files and %d playlist tracks into ontology",
            len(catalog), nested_count,
        )

    def get_graph_snapshot(self) -> dict[str, list[dict[str, Any]]]:
        """Return the graph in a JSON-friendly form for the private review UI."""
        rows = self.store._db.execute(
            """SELECT id, entity_type, name, properties, created_at, updated_at
               FROM entities ORDER BY updated_at DESC, created_at DESC"""
        ).fetchall()
        link_rows = self.store._db.execute(
            "SELECT id, relationship_type, from_entity, to_entity, properties, created_at FROM links ORDER BY created_at"
        ).fetchall()
        return {
            "nodes": [
                {
                    "id": row["id"], "type": row["entity_type"], "name": row["name"],
                    "properties": json.loads(row["properties"]), "created_at": row["created_at"],
                    "updated_at": row["updated_at"],
                }
                for row in rows
            ],
            "links": [
                {
                    "id": row["id"], "type": row["relationship_type"],
                    "from": row["from_entity"], "to": row["to_entity"],
                    "properties": json.loads(row["properties"]) if row["properties"] else {},
                    "created_at": row["created_at"],
                }
                for row in link_rows
            ],
        }

    # ─── Media Catalog ────────────────────────────────────────────────────────

    def upsert_media(self, file_id: str, title: str, filename: str, **props: Any) -> str:
        """Create or update a media entity. Returns entity ID."""
        properties = {"filename": filename, "title": title, **props}
        existing = self.store.get_entity_by_identifier("media_file", file_id)
        if existing and existing.name == title and existing.properties == properties:
            # Do not make a catalog scan look like a content update.
            return existing.id
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

    def record_message(
        self,
        text: str,
        conversation_id: str,
        topics: list[str],
        media_id: str | None = None,
        playback_session_id: str | None = None,
    ) -> str:
        """Record a user message and its extracted topics/media as graph nodes and edges.

        Creates:
          [message] --mentions--> [topic]  (for each topic)
          [message] --triggered--> [media] (if media was played)
          [media] --about--> [topic]       (links media to topics from this message)

        Returns the message entity ID for linking facts.
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

        if playback_session_id and self.store.get_entity(playback_session_id):
            self.store.create_link("started", msg_entity.id, playback_session_id)

        return msg_entity.id

    # ─── Resumable Playback ──────────────────────────────────────────────────

    def begin_playback(
        self, playlist_id: str, track_count: int, operation: str = "play"
    ) -> dict[str, Any]:
        """Create or resume a playback session and return its cursor."""
        now = datetime.now(timezone.utc).isoformat()
        playlist = self.store.get_entity_by_identifier("playlist_id", playlist_id)
        if not playlist:
            raise KeyError(f"Unknown playlist: {playlist_id}")

        session = None
        if operation == "resume":
            row = self.store._db.execute(
                """SELECT id FROM entities
                   WHERE entity_type = 'playback_session'
                     AND json_extract(properties, '$.playlist_id') = ?
                     AND json_extract(properties, '$.status') IN
                         ('playing', 'stopped', 'interrupted', 'failed')
                   ORDER BY updated_at DESC LIMIT 1""",
                (playlist_id,),
            ).fetchone()
            if row:
                session = self.store.get_entity(row["id"])

        if not session:
            # Starting over makes older attempts for this same playlist
            # historical rather than candidates for a future resume.
            prior_rows = self.store._db.execute(
                """SELECT id FROM entities
                   WHERE entity_type = 'playback_session'
                     AND json_extract(properties, '$.playlist_id') = ?
                     AND json_extract(properties, '$.status') IN
                         ('playing', 'stopped', 'interrupted', 'failed')""",
                (playlist_id,),
            ).fetchall()
            for row in prior_rows:
                self._set_playback_status(row["id"], "superseded")

        # A single media player can have only one active playlist.
        active_rows = self.store._db.execute(
            """SELECT id FROM entities
               WHERE entity_type = 'playback_session'
                 AND json_extract(properties, '$.status') = 'playing'"""
        ).fetchall()
        for row in active_rows:
            if not session or row["id"] != session.id:
                self._set_playback_status(row["id"], "interrupted")

        if session:
            props = {
                **session.properties,
                "track_count": track_count,
                "status": "playing",
                "updated_at": now,
            }
            self.store.update_entity(session.id, properties=props)
            session_id = session.id
            start_index = min(int(props.get("next_track_index", 0)), track_count)
        else:
            session = self.store.create_entity(
                "playback_session",
                f"{playlist.name} playback",
                properties={
                    "playlist_id": playlist_id,
                    "next_track_index": 0,
                    "track_count": track_count,
                    "status": "playing",
                    "started_at": now,
                    "updated_at": now,
                },
            )
            self.store.create_link("uses", session.id, playlist.id)
            session_id = session.id
            start_index = 0

        return {"session_id": session_id, "start_index": start_index}

    def update_playback(
        self, session_id: str, event: str, track_index: int | None = None
    ) -> dict[str, Any]:
        """Apply a trusted playback event from Home Assistant."""
        session = self.store.get_entity(session_id)
        if not session or session.entity_type != "playback_session":
            raise KeyError(f"Unknown playback session: {session_id}")

        props = dict(session.properties)
        if props.get("status") in {"completed", "superseded"}:
            return props
        if event == "track_completed":
            if track_index is None or track_index < 0:
                raise ValueError("track_index is required for track_completed")
            # A stop/switch decision wins over a late player-state callback. At
            # worst the last track is repeated; we never skip unheard material.
            if props.get("status") != "playing":
                return props
            next_index = max(int(props.get("next_track_index", 0)), track_index + 1)
            props["next_track_index"] = min(next_index, int(props["track_count"]))
            if props["next_track_index"] >= int(props["track_count"]):
                props["status"] = "completed"
                props["completed_at"] = datetime.now(timezone.utc).isoformat()
            else:
                props["status"] = "playing"
        elif event in {"stopped", "interrupted", "failed"}:
            props["status"] = event
        else:
            raise ValueError(f"Unknown playback event: {event}")

        props["updated_at"] = datetime.now(timezone.utc).isoformat()
        self.store.update_entity(session_id, properties=props)
        return props

    def _set_playback_status(self, session_id: str, status: str) -> None:
        session = self.store.get_entity(session_id)
        if not session:
            return
        props = {
            **session.properties,
            "status": status,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        self.store.update_entity(session_id, properties=props)

    def stop_active_playback(self) -> list[str]:
        """Mark active sessions stopped and return their IDs."""
        rows = self.store._db.execute(
            """SELECT id FROM entities
               WHERE entity_type = 'playback_session'
                 AND json_extract(properties, '$.status') = 'playing'"""
        ).fetchall()
        session_ids = [row["id"] for row in rows]
        for session_id in session_ids:
            self._set_playback_status(session_id, "stopped")
        return session_ids

    def build_playback_prompt(self, limit: int = 3) -> str:
        """Return compact recent incomplete playback state for the LLM."""
        rows = self.store._db.execute(
            """SELECT properties FROM entities
               WHERE entity_type = 'playback_session'
                 AND json_extract(properties, '$.status') IN
                     ('playing', 'stopped', 'interrupted', 'failed')
               ORDER BY updated_at DESC LIMIT ?""",
            (limit,),
        ).fetchall()
        if not rows:
            return "No resumable playback sessions."

        lines = ["Recent resumable playback:"]
        playlists = scan_playlist_catalog()
        for row in rows:
            props = json.loads(row["properties"])
            playlist_id = props["playlist_id"]
            next_index = int(props.get("next_track_index", 0))
            track_count = int(props.get("track_count", 0))
            tracks = playlists.get(playlist_id, [])
            next_title = tracks[next_index]["title"] if next_index < len(tracks) else "end"
            lines.append(
                f"- {playlist_id}: {next_index} of {track_count} completed; "
                f"next: {next_title}; status: {props.get('status', 'interrupted')}"
            )
        return "\n".join(lines)

    # ─── Facts ────────────────────────────────────────────────────────────────

    def record_facts(self, facts: list[dict[str, Any]], message_entity_id: str) -> None:
        """Record extracted facts and link them to the source message.

        Each fact dict has: subject, relation, object, confidence.
        Deduplicates on (subject, relation, object). If the fact already exists,
        adds a new 'supports' edge and updates confidence.
        """
        for fact_data in facts:
            subject = fact_data.get("subject", "").strip()
            relation = fact_data.get("relation", "").strip()
            obj = fact_data.get("object", "").strip()
            confidence = float(fact_data.get("confidence", 0.5))

            if not subject or not relation or not obj:
                continue

            fact_key = f"{subject.lower()}|{relation.lower()}|{obj.lower()}"
            existing = self.store.get_entity_by_identifier("fact_key", fact_key)

            if existing:
                # Add supporting evidence and update confidence
                self.store.create_link("supports", message_entity_id, existing.id)
                old_confidence = existing.properties.get("confidence", 0.5)
                new_confidence = old_confidence + (1 - old_confidence) * confidence * 0.3
                self.store.update_entity(existing.id, properties={
                    **existing.properties,
                    "confidence": round(min(new_confidence, 0.99), 2),
                })
                logger.info("Fact reinforced: %r (confidence %.2f -> %.2f)", existing.name, old_confidence, new_confidence)
            else:
                # Create new fact entity
                name = f"{subject} {relation.replace('_', ' ')} {obj}"
                fact_entity = self.store.create_entity(
                    "fact", name,
                    properties={
                        "subject": subject,
                        "relation": relation,
                        "object": obj,
                        "confidence": round(confidence, 2),
                    },
                )
                self.store.set_identifier(fact_entity.id, "fact_key", fact_key)
                self.store.create_link("supports", message_entity_id, fact_entity.id)
                logger.info("Fact created: %r (confidence %.2f)", name, confidence)

    def get_known_facts(self, limit: int = MAX_FACTS_IN_PROMPT) -> list[dict[str, Any]]:
        """Get high-confidence facts ranked by evidence count.

        Filters by confidence threshold, ranks by number of supporting messages.
        """
        db = self.store._db
        rows = db.execute("""
            SELECT f.id, f.name, f.properties,
                   COUNT(l.id) as evidence_count
            FROM entities f
            JOIN links l ON l.to_entity = f.id AND l.relationship_type = 'supports'
            WHERE f.entity_type = 'fact'
              AND json_extract(f.properties, '$.confidence') >= ?
            GROUP BY f.id
            ORDER BY evidence_count DESC
            LIMIT ?
        """, (FACT_CONFIDENCE_THRESHOLD, limit)).fetchall()
        return [
            {
                "name": row["name"],
                "confidence": json.loads(row["properties"]).get("confidence", 0),
                "evidence_count": row["evidence_count"],
            }
            for row in rows
        ]

    # ─── Family Values & Kid Events ─────────────────────────────────────────

    def upsert_family_value(
        self,
        name: str,
        description: str = "",
        guidance: str = "",
        key: str | None = None,
        enabled: bool = True,
    ) -> dict[str, Any]:
        """Create or update one family value used by the chat prompt."""
        name = name.strip()
        if not name:
            raise ValueError("Family value name is required")
        value_key = (key or _slugify(name)).strip().lower()
        properties = {
            "key": value_key,
            "description": description.strip(),
            "guidance": guidance.strip(),
            "enabled": bool(enabled),
        }
        entity, _ = self.store.upsert_entity(
            "family_value",
            name,
            properties=properties,
            match_on=("family_value_key", value_key),
        )
        return self._family_value_to_dict(entity)

    def get_family_values(
        self,
        enabled_only: bool = False,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        """Return family values, newest updates first."""
        rows = self.store._db.execute(
            """SELECT id, entity_type, name, properties, summary, created_at, updated_at
               FROM entities
               WHERE entity_type = 'family_value'
               ORDER BY updated_at DESC, created_at DESC"""
        ).fetchall()
        values = [self._family_value_to_dict(self.store._row_to_entity(row)) for row in rows]
        if enabled_only:
            values = [value for value in values if value["enabled"]]
        return values[:limit] if limit else values

    def build_family_values_prompt(self) -> str:
        """Return compact family value guidance for the LLM."""
        values = self.get_family_values(
            enabled_only=True,
            limit=MAX_FAMILY_VALUES_IN_PROMPT,
        )
        if not values:
            return "No family values have been configured yet."

        lines = ["Family values for encouragement and correction:"]
        for value in values:
            description = value.get("description") or "No short description."
            guidance = value.get("guidance") or "Apply this value warmly and concretely."
            lines.append(f"- {value['key']}: {value['name']} — {description} Guidance: {guidance}")
        return "\n".join(lines)

    def record_kid_events(
        self,
        events: list[dict[str, Any]],
        message_entity_id: str,
        conversation_id: str,
        source_text: str,
    ) -> list[dict[str, Any]]:
        """Record parent-described kid events and link them to matching family values."""
        recorded: list[dict[str, Any]] = []
        now = datetime.now(timezone.utc).isoformat()
        for event_data in events:
            child_name = str(event_data.get("child_name", "")).strip()
            summary = str(event_data.get("summary", "")).strip()
            if not child_name or not summary:
                continue

            event_type = str(event_data.get("event_type", "observation")).strip().lower()
            if event_type not in VALID_KID_EVENT_TYPES:
                event_type = "observation"

            matched_value_keys = [
                str(key).strip().lower()
                for key in (event_data.get("matched_value_keys") or [])
                if str(key).strip()
            ][:3]
            parent_note = str(event_data.get("parent_note", "")).strip()
            confidence = _safe_float(event_data.get("confidence", 0.7), default=0.7)
            properties = {
                "child_name": child_name,
                "summary": summary,
                "event_type": event_type,
                "matched_value_keys": matched_value_keys,
                "parent_note": parent_note,
                "confidence": round(max(0.0, min(confidence, 1.0)), 2),
                "conversation_id": conversation_id,
                "source_text": source_text,
                "timestamp": now,
            }
            entity = self.store.create_entity(
                "kid_event",
                f"{child_name}: {summary[:80]}",
                properties=properties,
                created_at=now,
            )
            self.store.create_link("reports", message_entity_id, entity.id)

            for value_key in matched_value_keys:
                value = self.store.get_entity_by_identifier("family_value_key", value_key)
                if value:
                    self.store.upsert_link("reflects", entity.id, value.id)

            event = {"id": entity.id, "name": entity.name, **properties}
            recorded.append(event)
            logger.info(
                "Kid event recorded: child=%r type=%s values=%r",
                child_name,
                event_type,
                matched_value_keys,
            )
        return recorded

    def get_kid_events(
        self,
        child_name: str | None = None,
        limit: int = MAX_KID_EVENTS_RETURNED,
    ) -> list[dict[str, Any]]:
        """Return recent kid event log entries."""
        limit = max(1, min(limit, MAX_KID_EVENTS_RETURNED))
        params: list[Any] = []
        where = "WHERE entity_type = 'kid_event'"
        if child_name:
            where += " AND lower(json_extract(properties, '$.child_name')) = lower(?)"
            params.append(child_name.strip())
        rows = self.store._db.execute(
            f"""SELECT id, name, properties, created_at, updated_at
                FROM entities
                {where}
                ORDER BY created_at DESC
                LIMIT ?""",
            (*params, limit),
        ).fetchall()
        events: list[dict[str, Any]] = []
        for row in rows:
            props = json.loads(row["properties"])
            events.append({
                "id": row["id"],
                "name": row["name"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
                **props,
            })
        return events

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
        """Build a context string about known facts and recent interests for the system prompt."""
        parts: list[str] = []

        facts = self.get_known_facts()
        if facts:
            fact_sentences = ". ".join(f["name"] for f in facts)
            parts.append(f"Things you know about this family: {fact_sentences}.")

        interests = self.get_recent_interests()
        if interests:
            top_topics = [i["name"] for i in interests[:6]]
            parts.append(f"Topics we've talked about recently: {', '.join(top_topics)}.")

        return "\n".join(parts)

    def _family_value_to_dict(self, entity: Any) -> dict[str, Any]:
        props = entity.properties
        return {
            "id": entity.id,
            "name": entity.name,
            "key": props.get("key", ""),
            "description": props.get("description", ""),
            "guidance": props.get("guidance", ""),
            "enabled": bool(props.get("enabled", True)),
            "created_at": entity.created_at,
            "updated_at": entity.updated_at,
        }


def _slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")
    return slug or "family_value"


def _safe_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
