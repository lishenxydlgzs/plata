"""OntologyStore: SQLite-backed CRUD for entities, links, identifiers, settings, and graph traversal."""

from __future__ import annotations

import json
import sqlite3
import uuid
from collections import deque
from datetime import datetime, timezone
from typing import Any, Protocol

from .types import Entity, EntityFilter, EntityGraph, EntityIdentifier, EntityType, Link, LinkType


STUB_SUMMARY_PROPS = ["title", "preview", "displayName", "description", "status", "owner"]
PERSON_SUMMARY_PROPS = ["displayName", "email", "slackAlias"]


def build_stub_summary(entity_type: str, name: str, properties: dict[str, Any] | None = None) -> str:
    prop_keys = PERSON_SUMMARY_PROPS if entity_type == "person" else STUB_SUMMARY_PROPS
    parts: list[str] = []
    if properties:
        for key in prop_keys:
            val = properties.get(key)
            if val is not None and val != "":
                parts.append(f"{key}: {val}")
    props_clause = f" {'. '.join(parts)}." if parts else ""
    safe_name = name.replace('"', '\\"')
    return f'A {entity_type} named "{safe_name}".{props_clause}'


class TypeRegistry(Protocol):
    def get_entity_types(self) -> list[EntityType]: ...
    def get_entity_type(self, id: str) -> EntityType | None: ...
    def get_link_types(self) -> list[LinkType]: ...
    def get_link_type(self, id: str) -> LinkType | None: ...


class OntologyStore:
    def __init__(self, db: sqlite3.Connection, types: TypeRegistry) -> None:
        self._db = db
        self._db.row_factory = sqlite3.Row
        self._types = types

    def get_all_entity_types(self) -> list[EntityType]:
        return self._types.get_entity_types()

    def get_entity_type(self, id: str) -> EntityType | None:
        return self._types.get_entity_type(id)

    def get_all_link_types(self) -> list[LinkType]:
        return self._types.get_link_types()

    def get_link_type(self, id: str) -> LinkType | None:
        return self._types.get_link_type(id)

    # ─── Entities ─────────────────────────────────────────────────────────────

    def create_entity(
        self,
        entity_type: str,
        name: str,
        properties: dict[str, Any] | None = None,
        summary: str | None = None,
        created_at: str | None = None,
    ) -> Entity:
        id = str(uuid.uuid4())
        ts = created_at or _now()
        props = properties or {}
        final_summary = summary or build_stub_summary(entity_type, name, props)
        self._db.execute(
            "INSERT INTO entities (id, entity_type, name, properties, summary, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (id, entity_type, name, json.dumps(props), final_summary, ts, ts),
        )
        self._db.commit()
        return Entity(id=id, entity_type=entity_type, name=name, properties=props, summary=final_summary, created_at=ts, updated_at=ts)

    def get_entity(self, id: str) -> Entity | None:
        row = self._db.execute(
            "SELECT id, entity_type, name, properties, summary, created_at, updated_at FROM entities WHERE id = ?",
            (id,),
        ).fetchone()
        if not row:
            return None
        return self._row_to_entity(row)

    def query_entities(self, filter: EntityFilter) -> list[Entity]:
        sql = "SELECT id, entity_type, name, properties, summary, created_at, updated_at FROM entities WHERE 1=1"
        params: list[Any] = []
        if filter.entity_type:
            sql += " AND entity_type = ?"
            params.append(filter.entity_type)
        if filter.name_contains:
            sql += " AND name LIKE ?"
            params.append(f"%{filter.name_contains}%")
        sql += " ORDER BY created_at DESC"
        if filter.limit:
            sql += " LIMIT ?"
            params.append(filter.limit)
        if filter.offset:
            sql += " OFFSET ?"
            params.append(filter.offset)
        rows = self._db.execute(sql, params).fetchall()
        return [self._row_to_entity(r) for r in rows]

    def update_entity(self, id: str, *, name: str | None = None, properties: dict[str, Any] | None = None, summary: str | None = None) -> bool:
        if not self.get_entity(id):
            return False
        now = _now()
        fields: list[str] = []
        values: list[Any] = []
        if name is not None:
            fields.append("name = ?")
            values.append(name)
        if properties is not None:
            fields.append("properties = ?")
            values.append(json.dumps(properties))
        if summary is not None:
            fields.append("summary = ?")
            values.append(summary)
        fields.append("updated_at = ?")
        values.append(now)
        values.append(id)
        self._db.execute(f"UPDATE entities SET {', '.join(fields)} WHERE id = ?", values)
        self._db.commit()
        return True

    def backdate_created_at(self, id: str, created_at: str) -> None:
        self._db.execute(
            "UPDATE entities SET created_at = ? WHERE id = ? AND created_at > ?",
            (created_at, id, created_at),
        )
        self._db.commit()

    def search_entities(self, query: str, limit: int = 50) -> list[Entity]:
        try:
            safe_query = query
            for ch in '"\'*^{}()@.':
                safe_query = safe_query.replace(ch, " ")
            safe_query = safe_query.strip()
            if not safe_query:
                return []
            tokens = [w for w in safe_query.split() if len(w) >= 2]
            if not tokens:
                return []
            fts_query = " ".join(f"{{name summary}}:{w}*" for w in tokens)
            sql = """
                SELECT e.id, e.entity_type, e.name, e.properties, e.summary,
                       e.created_at, e.updated_at
                FROM entities_fts
                JOIN entities e ON e.rowid = entities_fts.rowid
                WHERE entities_fts MATCH ?
                ORDER BY bm25(entities_fts, 0.0, 5.0, 1.0, 0.0)
                LIMIT ?
            """
            rows = self._db.execute(sql, (fts_query, limit)).fetchall()
            return [self._row_to_entity(r) for r in rows]
        except sqlite3.OperationalError:
            return self.query_entities(EntityFilter(name_contains=query, limit=limit))

    def upsert_entity(
        self,
        entity_type: str,
        name: str,
        properties: dict[str, Any] | None = None,
        summary: str | None = None,
        *,
        match_on: tuple[str, str] | None = None,
    ) -> tuple[Entity, bool]:
        """Insert or update an entity. Returns (entity, created).

        If match_on is provided as (system, external_id), looks up by identifier.
        Otherwise matches by (entity_type, name).
        """
        existing: Entity | None = None
        if match_on:
            existing = self.get_entity_by_identifier(match_on[0], match_on[1])
        else:
            rows = self._db.execute(
                "SELECT id, entity_type, name, properties, summary, created_at, updated_at FROM entities WHERE entity_type = ? AND name = ?",
                (entity_type, name),
            ).fetchall()
            if rows:
                existing = self._row_to_entity(rows[0])

        if existing:
            props = properties or {}
            merged_props = {**existing.properties, **props}
            final_summary = summary or build_stub_summary(entity_type, name, merged_props)
            self.update_entity(existing.id, name=name, properties=merged_props, summary=final_summary)
            updated = self.get_entity(existing.id)
            return updated, False  # type: ignore[return-value]
        else:
            entity = self.create_entity(entity_type, name, properties, summary)
            if match_on:
                self.set_identifier(entity.id, match_on[0], match_on[1])
            return entity, True

    def delete_entity(self, id: str) -> bool:
        cursor = self._db.execute("DELETE FROM entities WHERE id = ?", (id,))
        self._db.commit()
        return cursor.rowcount > 0

    # ─── Links ────────────────────────────────────────────────────────────────

    def create_link(
        self,
        relationship_type: str,
        from_entity: str,
        to_entity: str,
        properties: dict[str, Any] | None = None,
    ) -> Link:
        id = str(uuid.uuid4())
        now = _now()
        self._db.execute(
            "INSERT INTO links (id, relationship_type, from_entity, to_entity, properties, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (id, relationship_type, from_entity, to_entity, json.dumps(properties) if properties else None, now),
        )
        self._db.execute("UPDATE entities SET updated_at = ? WHERE id = ?", (now, from_entity))
        self._db.execute("UPDATE entities SET updated_at = ? WHERE id = ?", (now, to_entity))
        self._db.commit()
        return Link(id=id, relationship_type=relationship_type, from_entity=from_entity, to_entity=to_entity, properties=properties, created_at=now)

    def upsert_link(
        self,
        relationship_type: str,
        from_entity: str,
        to_entity: str,
        properties: dict[str, Any] | None = None,
    ) -> tuple[Link, bool]:
        """Insert or update a link. Returns (link, created).

        Matches on (relationship_type, from_entity, to_entity) which has a UNIQUE constraint.
        If the link exists, its properties are merged and updated.
        """
        row = self._db.execute(
            "SELECT id, relationship_type, from_entity, to_entity, properties, created_at FROM links WHERE relationship_type = ? AND from_entity = ? AND to_entity = ?",
            (relationship_type, from_entity, to_entity),
        ).fetchone()
        if row:
            existing = self._row_to_link(row)
            merged_props = {**(existing.properties or {}), **(properties or {})} if properties or existing.properties else None
            now = _now()
            self._db.execute(
                "UPDATE links SET properties = ? WHERE id = ?",
                (json.dumps(merged_props) if merged_props else None, existing.id),
            )
            self._db.execute("UPDATE entities SET updated_at = ? WHERE id = ?", (now, from_entity))
            self._db.execute("UPDATE entities SET updated_at = ? WHERE id = ?", (now, to_entity))
            self._db.commit()
            return Link(id=existing.id, relationship_type=relationship_type, from_entity=from_entity, to_entity=to_entity, properties=merged_props, created_at=existing.created_at), False
        else:
            link = self.create_link(relationship_type, from_entity, to_entity, properties)
            return link, True

    def get_link(self, id: str) -> Link | None:
        row = self._db.execute(
            "SELECT id, relationship_type, from_entity, to_entity, properties, created_at FROM links WHERE id = ?",
            (id,),
        ).fetchone()
        if not row:
            return None
        return self._row_to_link(row)

    def get_entity_links(self, entity_id: str) -> list[Link]:
        rows = self._db.execute(
            "SELECT id, relationship_type, from_entity, to_entity, properties, created_at FROM links WHERE from_entity = ? OR to_entity = ?",
            (entity_id, entity_id),
        ).fetchall()
        return [self._row_to_link(r) for r in rows]

    def get_linked_entities_by_type(self, entity_ids: list[str], target_types: list[str], limit: int) -> list[dict[str, Any]]:
        if not entity_ids or not target_types:
            return []
        id_ph = ",".join("?" * len(entity_ids))
        type_ph = ",".join("?" * len(target_types))
        sql = f"""
            WITH neighbours AS (
                SELECT to_entity AS neighbour_id, from_entity AS via_id FROM links WHERE from_entity IN ({id_ph})
                UNION ALL
                SELECT from_entity AS neighbour_id, to_entity AS via_id FROM links WHERE to_entity IN ({id_ph})
            )
            SELECT e.id, e.entity_type, e.name, e.properties, e.summary,
                   e.created_at, e.updated_at, MIN(n.via_id) AS via_id
            FROM neighbours n
            JOIN entities e ON e.id = n.neighbour_id
            WHERE e.entity_type IN ({type_ph}) AND e.id NOT IN ({id_ph})
            GROUP BY e.id
            ORDER BY e.updated_at DESC
            LIMIT ?
        """
        params = [*entity_ids, *entity_ids, *target_types, *entity_ids, limit]
        rows = self._db.execute(sql, params).fetchall()
        return [{"entity": self._row_to_entity(r), "via_entity_id": r["via_id"]} for r in rows]

    def get_all_links(self) -> list[Link]:
        rows = self._db.execute(
            "SELECT id, relationship_type, from_entity, to_entity, properties, created_at FROM links ORDER BY created_at DESC"
        ).fetchall()
        return [self._row_to_link(r) for r in rows]

    def delete_link(self, id: str) -> bool:
        cursor = self._db.execute("DELETE FROM links WHERE id = ?", (id,))
        self._db.commit()
        return cursor.rowcount > 0

    # ─── Graph Traversal ──────────────────────────────────────────────────────

    def get_entity_graph(self, entity_id: str, depth: int = 2) -> EntityGraph:
        entities: dict[str, Entity] = {}
        links: list[Link] = []
        visited: set[str] = set()
        queue: deque[tuple[str, int]] = deque([(entity_id, 0)])

        while queue:
            current_id, level = queue.popleft()
            if current_id in visited or level > depth:
                continue
            visited.add(current_id)
            entity = self.get_entity(current_id)
            if not entity:
                continue
            entities[current_id] = entity
            if level < depth:
                for link in self.get_entity_links(current_id):
                    links.append(link)
                    next_id = link.to_entity if link.from_entity == current_id else link.from_entity
                    if next_id not in visited:
                        queue.append((next_id, level + 1))

        entity_types: dict[str, EntityType] = {}
        link_types: dict[str, LinkType] = {}
        for e in entities.values():
            t = self.get_entity_type(e.entity_type)
            if t:
                entity_types[t.id] = t
        for link in links:
            t = self.get_link_type(link.relationship_type)
            if t:
                link_types[t.id] = t

        return EntityGraph(
            entities=list(entities.values()),
            links=links,
            entity_types=entity_types,
            relationship_types=link_types,
        )

    # ─── Settings ─────────────────────────────────────────────────────────────

    def get_setting(self, key: str) -> str | None:
        row = self._db.execute("SELECT value FROM ontology_settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None

    def set_setting(self, key: str, value: str) -> None:
        now = _now()
        self._db.execute(
            "INSERT INTO ontology_settings (key, value, updated_at) VALUES (?, ?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
            (key, value, now),
        )
        self._db.commit()

    # ─── Identifiers ──────────────────────────────────────────────────────────

    def set_identifier(self, entity_id: str, system: str, external_id: str) -> None:
        now = _now()
        self._db.execute(
            "INSERT OR REPLACE INTO entity_identifiers (entity_id, system, external_id, created_at) VALUES (?, ?, ?, ?)",
            (entity_id, system, external_id, now),
        )
        self._db.commit()

    def remove_identifier(self, entity_id: str, system: str) -> bool:
        cursor = self._db.execute(
            "DELETE FROM entity_identifiers WHERE entity_id = ? AND system = ?",
            (entity_id, system),
        )
        self._db.commit()
        return cursor.rowcount > 0

    def get_identifiers(self, entity_id: str) -> list[EntityIdentifier]:
        rows = self._db.execute(
            "SELECT entity_id, system, external_id, created_at FROM entity_identifiers WHERE entity_id = ?",
            (entity_id,),
        ).fetchall()
        return [EntityIdentifier(entity_id=r["entity_id"], system=r["system"], external_id=r["external_id"], created_at=r["created_at"]) for r in rows]

    def get_entity_by_identifier(self, system: str, external_id: str) -> Entity | None:
        row = self._db.execute(
            """
            SELECT e.id, e.entity_type, e.name, e.properties, e.summary, e.created_at, e.updated_at
            FROM entity_identifiers i
            JOIN entities e ON e.id = i.entity_id
            WHERE i.system = ? AND i.external_id = ?
            """,
            (system, external_id),
        ).fetchone()
        if not row:
            return None
        return self._row_to_entity(row)

    def find_entities_sharing_identifiers(self, entity_id: str) -> list[dict[str, Any]]:
        rows = self._db.execute(
            """
            SELECT DISTINCT e.id, e.entity_type, e.name, e.properties, e.summary, e.created_at, e.updated_at,
                   i2.system AS shared_system, i2.external_id AS shared_external_id
            FROM entity_identifiers i1
            JOIN entity_identifiers i2 ON i1.system = i2.system AND i1.external_id = i2.external_id AND i2.entity_id != i1.entity_id
            JOIN entities e ON e.id = i2.entity_id
            WHERE i1.entity_id = ?
            """,
            (entity_id,),
        ).fetchall()
        return [
            {"entity": self._row_to_entity(r), "shared_system": r["shared_system"], "shared_external_id": r["shared_external_id"]}
            for r in rows
        ]

    # ─── Bulk Operations ──────────────────────────────────────────────────────

    def clear_all_data(self) -> None:
        self._db.executescript("""
            DROP TRIGGER IF EXISTS entities_fts_ad;
            DROP TRIGGER IF EXISTS entities_fts_au;
            DROP TRIGGER IF EXISTS entities_fts_ai;
        """)
        self._db.execute("DELETE FROM links")
        self._db.execute("DELETE FROM entities")
        try:
            self._db.execute("DELETE FROM entities_fts")
        except sqlite3.OperationalError:
            pass
        self._db.commit()

    # ─── Private ──────────────────────────────────────────────────────────────

    def _row_to_entity(self, row: sqlite3.Row) -> Entity:
        return Entity(
            id=row["id"],
            entity_type=row["entity_type"],
            name=row["name"],
            properties=json.loads(row["properties"]),
            summary=row["summary"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def _row_to_link(self, row: sqlite3.Row) -> Link:
        return Link(
            id=row["id"],
            relationship_type=row["relationship_type"],
            from_entity=row["from_entity"],
            to_entity=row["to_entity"],
            properties=json.loads(row["properties"]) if row["properties"] else None,
            created_at=row["created_at"],
        )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
