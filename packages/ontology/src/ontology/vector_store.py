"""VectorStore: semantic search via LanceDB with injectable embeddings."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

EmbedFn = Callable[[str], Awaitable[list[float]]]


@dataclass
class EmbeddingRecord:
    id: str
    entity_type: str
    name: str
    summary: str
    content: str
    vector: list[float]


@dataclass
class SearchResult:
    id: str
    entity_type: str
    name: str
    score: float


REQUIRED_COLUMNS = ["id", "entityType", "name", "summary", "content", "vector"]


class VectorStore:
    def __init__(
        self,
        db_path: str,
        embed: EmbedFn,
        table_name: str = "entity_embeddings",
        max_input_chars: int = 6000,
    ) -> None:
        self._db_path = db_path
        self._table_name = table_name
        self._embed = embed
        self._max_input_chars = max_input_chars
        self._db: Any = None
        self._table: Any = None
        self._fts_index_created = False

    def _get_db(self) -> Any:
        if self._db is None:
            import lancedb

            os.makedirs(self._db_path, exist_ok=True)
            self._db = lancedb.connect(self._db_path)
        return self._db

    def _get_or_create_table(self, seed_record: dict[str, Any] | None = None) -> Any:
        if self._table is not None:
            return self._table
        db = self._get_db()
        table_names = db.table_names()
        if self._table_name in table_names:
            existing = db.open_table(self._table_name)
            schema = existing.schema
            field_names = [f.name for f in schema]
            missing = [c for c in REQUIRED_COLUMNS if c not in field_names]
            if missing:
                db.drop_table(self._table_name)
                if seed_record:
                    self._table = db.create_table(self._table_name, [seed_record])
                    return self._table
                return None
            self._table = existing
            return self._table
        elif seed_record:
            self._table = db.create_table(self._table_name, [seed_record])
            return self._table
        return None

    async def embed_text(self, text: str) -> list[float]:
        truncated = text[: self._max_input_chars] if len(text) > self._max_input_chars else text
        return await self._embed(truncated)

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [await self.embed_text(t) for t in texts]

    async def upsert_vectors(self, records: list[EmbeddingRecord]) -> None:
        if not records:
            return
        data = [
            {
                "id": r.id,
                "entityType": r.entity_type,
                "name": r.name,
                "summary": r.summary,
                "content": r.content,
                "vector": r.vector,
            }
            for r in records
        ]
        t = self._get_or_create_table(data[0])
        if not t:
            return
        t.merge_insert("id").when_matched_update_all().when_not_matched_insert_all().execute(data)

    async def search(self, query: str, top_k: int = 10, entity_types: list[str] | None = None) -> list[SearchResult]:
        t = self._get_or_create_table()
        if not t:
            return []
        try:
            query_vector = await self.embed_text(query)
            search_builder = t.search(query_vector).metric("cosine").limit(top_k)
            if entity_types:
                filter_clause = " OR ".join(f"entityType = '{et.replace(chr(39), chr(39)*2)}'" for et in entity_types)
                search_builder = search_builder.where(filter_clause)
            results = search_builder.to_pandas()
            return [
                SearchResult(
                    id=row["id"],
                    entity_type=row["entityType"],
                    name=row["name"],
                    score=1 - (row.get("_distance", 0)),
                )
                for _, row in results.iterrows()
            ]
        except Exception:
            return []

    async def hybrid_search(self, query: str, top_k: int = 10, entity_types: list[str] | None = None) -> list[SearchResult]:
        t = self._get_or_create_table()
        if not t:
            return []
        try:
            self._ensure_fts_index(t)
            query_vector = await self.embed_text(query)
            import lancedb

            reranker = lancedb.rerankers.RRFReranker(k=60)
            search_builder = (
                t.search(query_vector, query_type="hybrid")
                .metric("cosine")
                .limit(top_k)
                .rerank(reranker)
            )
            if entity_types:
                filter_clause = " OR ".join(f"entityType = '{et.replace(chr(39), chr(39)*2)}'" for et in entity_types)
                search_builder = search_builder.where(filter_clause)
            results = search_builder.to_pandas()
            return [
                SearchResult(
                    id=row["id"],
                    entity_type=row["entityType"],
                    name=row["name"],
                    score=row.get("_relevance_score", 1 - row.get("_distance", 0)),
                )
                for _, row in results.iterrows()
            ]
        except Exception:
            return await self.search(query, top_k, entity_types)

    async def delete_vector(self, entity_id: str) -> None:
        t = self._get_or_create_table()
        if not t:
            return
        t.delete(f"id = '{entity_id.replace(chr(39), chr(39)*2)}'")

    async def get_vector_count(self) -> int:
        t = self._get_or_create_table()
        if not t:
            return 0
        return t.count_rows()

    def _ensure_fts_index(self, t: Any) -> None:
        if self._fts_index_created:
            return
        try:
            t.create_fts_index(["name", "summary", "content"])
            self._fts_index_created = True
        except Exception as e:
            if "already exists" in str(e):
                self._fts_index_created = True

    @staticmethod
    def format_embedding_input(entity_type: str, name: str, summary: str | None = None) -> str:
        base = f"{entity_type}: {name}"
        return f"{base}. {summary}" if summary else base
