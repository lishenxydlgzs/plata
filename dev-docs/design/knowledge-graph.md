# Knowledge Graph

## Goal

Give Plata persistent memory across conversation sessions without additional LLM calls. The robot should remember what topics come up often, what the family has told it, and which media was played in what context.

## Architecture

The knowledge graph is built on the `ontology` library (`packages/ontology/`), using SQLite as the backing store at `$DB_DIR/ontology.db`.

### Entity Types

| Type | Purpose | Example |
|------|---------|---------|
| `message` | Every user utterance | "My dog is named Pang Pang" |
| `topic` | Subjects mentioned in conversations | "dinosaurs", "space" |
| `media` | Playable audio/video files | "Hokey Pokey", "ABC Song" |
| `fact` | Explicit statements about the family | "Pang Pang is a dog" |

### Link Types

| Link | From → To | Meaning |
|------|-----------|---------|
| `mentions` | message → topic | This message discussed this topic |
| `triggered` | message → media | This message caused this media to play |
| `about` | media → topic | This media is related to this topic |
| `supports` | message → fact | This message is evidence for this fact |

### Graph Example

```
[message: "Play me a dinosaur song"]
    --mentions--> [topic: dinosaurs]
    --triggered--> [media: Dinosaur Song]

[media: Dinosaur Song]
    --about--> [topic: dinosaurs]

[message: "My dog is named Pang Pang"]
    --mentions--> [topic: dogs]
    --supports--> [fact: Pang Pang is a dog]
```

## Data Flow

Every conversation turn:

1. User speaks → text arrives at `/conversation` endpoint
2. LLM returns JSON with `reply_text`, `media_ids`, `topics`, `facts`
3. A `message` entity is created with the user's text and timestamp
4. For each topic: upsert a `topic` entity, create `mentions` edge
5. If media was played: create `triggered` edge, plus `about` edges from media to topics
6. For each fact: upsert a `fact` entity (deduped by subject/relation/object), create `supports` edge

This all happens synchronously after the LLM response — zero extra API calls.

## Fact System

### Extraction

The LLM is instructed to extract 0-2 facts per message, only when the user explicitly states something. The prompt includes concrete examples of what to extract and what NOT to extract (inferences, preferences from behavior).

Each extracted fact includes:
- `subject`: the thing being described ("Pang Pang")
- `relation`: the relationship ("is_a")
- `object`: the value ("family dog")
- `confidence`: 0.0-1.0, how clearly the user stated this

### Storage

Facts are entities with a human-readable `name` (used directly in the prompt) and structured properties for deduplication:

```
Entity(
    entity_type="fact",
    name="Pang Pang is a family dog",
    properties={
        "subject": "Pang Pang",
        "relation": "is_a",
        "object": "family dog",
        "confidence": 0.9
    }
)
```

Deduplication key: `subject|relation|object` (lowercased), stored as an entity identifier.

### Confidence

- Initial confidence comes from the LLM's assessment at extraction time
- Each new supporting message increases confidence: `new = old + (1 - old) * evidence_confidence * 0.3`
- Confidence asymptotically approaches 1.0 but never reaches it
- Facts below 0.7 confidence are excluded from the system prompt

### Ranking for Prompt Inclusion

Facts are **filtered** by confidence (≥ 0.7 threshold), then **ranked** by evidence count (number of `supports` edges). This surfaces facts that come up frequently in conversation, while keeping uncertain one-off extractions out.

## Topic Memory

### How Topics Are Ranked

Topics are scored by recency-weighted mention count using a single SQL query:

```sql
SELECT t.name,
       SUM(1.0 / (1.0 + julianday('now') - julianday(l.created_at))) as score
FROM links l
JOIN entities t ON t.id = l.to_entity
WHERE l.relationship_type = 'mentions'
  AND t.entity_type = 'topic'
GROUP BY t.id
ORDER BY score DESC
LIMIT 8
```

Each mention contributes `1 / (1 + age_in_days)`. A mention from today scores ~1.0, from yesterday ~0.5, from a week ago ~0.12. This means recent topics rise quickly while old ones decay without needing explicit cleanup.

## System Prompt Injection

The memory prompt is injected into the `{memory_context}` placeholder in the system prompt:

```
Things you know about this family: Pang Pang is a dog. Pang Pang loves chicken treats.
Topics we've talked about recently: dinosaurs, space, volcanoes.
```

Token budget: ~30-50 tokens for facts (max 6) + ~20 tokens for topics (max 6). Negligible impact on context window.

## Media Catalog Sync

On server startup, `sync_media_catalog()` scans the media directory and upserts each file as a `media` entity (matched by filename). This populates the graph with media nodes that can be linked to topics via the `about` relationship.

Media-topic links are created organically: when a message mentions topics AND triggers media playback, the media entity gets `about` edges to those topics. Over time, the system learns which media relates to which subjects based on actual usage patterns.

## LLM Token Budget

| Field | Tokens (typical) | When |
|-------|-------------------|------|
| `reply_text` | 30-80 | Always |
| `media_ids` | 10-40 | Only when playing media |
| `topics` | 5-15 | Most turns |
| `facts` | 20-60 | Only when user states facts (~10% of turns) |

`max_output_tokens` is set to 250 to accommodate all fields. Most responses use ~80-100 tokens.

## Files

| File | Purpose |
|------|---------|
| `packages/ontology/` | Reusable ontology library (entity graph, pipeline, vector store) |
| `packages/agent-server/src/agent_server/knowledge.py` | KnowledgeStore — graph operations specific to the bot |
| `packages/agent-server/src/agent_server/modes/chat.py` | Prompt building and fact/topic extraction from LLM response |

## Future Considerations

- **Fact contradiction handling**: Currently no mechanism to lower confidence when user contradicts a fact. Could add a `contradicts` link type or detect via the LLM.
- **Topic decay floor**: Topics that haven't been mentioned in weeks should eventually disappear entirely. The recency formula handles this naturally but extremely old topics with many mentions may still linger.
- **Entity resolution**: "Pang Pang" as a topic vs "Pang Pang" in a fact are separate entities. Could merge them via identifier-based dedup.
- **Child identification**: Facts could be scoped per-child if speaker identification is added later. The graph structure supports this (add a `child` entity, link facts to children).
