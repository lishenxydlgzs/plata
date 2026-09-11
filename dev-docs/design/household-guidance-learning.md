# Household guidance and learning memory

## Scope

One private household per installation. Preserve household-authored Markdown as
versioned guidance, independently of optional behavior and learning logging.
Existing family values and kid events remain available through legacy APIs; new
conversations use behavior_event and learning_event. No private document or
operational database is copied into the repository.

## Ontology

- guidance_document: immutable revision with document_key, version, content,
  application instructions and active flag. Updating creates a new revision and
  deactivates the previous revision. Historical event links remain valid.
- person: stable ID, display name and optional aliases; ambiguous names require
  clarification rather than merging people.
- behavior_event: report summary, person ID, occurrence description, recorded time,
  source message, self-identified reporter, interpretation and follow-up status.
- learning_event: same provenance plus topic, material, outcome (started,
  practiced, recalled, mastered, observation) and optional session ID. Mastery is
  stored only when explicitly reported, never inferred from combined ranges.
- message --reports--> event; event --involves--> person;
  learning_event --about--> topic; event --interpreted_using--> guidance_document.
  Interpretation links carry the document version, section and explanation.

Reporter identity is a claim from the conversation, never administrative authority.
Dates described in speech are preserved verbatim separately from server recording
time. Event extraction is bounded and validated; retries for the same source
message and event content are idempotent. Follow-ups preserve original reports.

## Conversation flow

Pass active guidance Markdown and separate application instructions to the model.
Include a directory of known people and learning topics, not global event logs.
The model may request one person/topic memory lookup. The backend validates IDs,
retrieves scoped history and invokes the response model once more. Only the final
response is spoken or persisted. This adds at most one LLM call on memory turns;
ordinary requests retain one call. A failed lookup response must not save a draft.
Session starts and progress reports are extracted from the current message only.
A follow-up can refer to a same-conversation learning session. Encourage the child
briefly, distinguish practice from recall, and avoid revisiting behavior incidents
unless relevant or requested. Conversation history is scoped to conversation ID.

## API and compatibility

GET/POST /api/guidance-documents; GET /api/guidance-documents?include_history=true.
GET/POST /api/people. GET /api/learning-events and /api/behavior-events accept
person_id and bounded limit; learning events also accept topic.
PATCH /api/behavior-events/{id} appends a parent review/repair update.
GET/PUT /api/memory-settings independently enable behavior and learning logging.
Legacy family-values/kid-events APIs and records remain readable; legacy values
are prompt fallback only when no active guidance document exists. There is no
automatic reinterpretation of historical records.

## Validation

Use isolated temporary databases and mocked LLMs. Cover immutable guidance
revisions, graph links, malformed extraction, person/topic isolation, logging
switches, session continuity, prior alphabet progress retrieval before response,
ambiguous people and preservation of legacy records. Run the backend test suite.

## Operation

Import a private file into a running server (the file stays outside the repo):

```bash
python3 scripts/import-guidance.py /path/to/private-guidance.md \
  --key household-values --title 'Household values'
```

Use `--server http://HOST:8200` for another installation. Reuse the same key to
create a new revision. `--inactive` deactivates the document; old revisions stay
readable with include_history=true. Content is sent to the configured LLM as
part of the system prompt. The graph explorer displays new entities and links.
The management APIs follow the existing private-server access model.

Memory defaults to enabled for both event types. PUT /api/memory-settings with
`{"behavior_logging": false, "learning_logging": true}` to use only learning
memory. Disabling a type stops new event records and conversational retrieval;
it does not delete existing events or disable the ordinary conversation transcript.

A parent can pre-create a person using POST /api/people with name and aliases.
Otherwise an unambiguous new name in a validated event creates a person. Duplicate
names remain separate and require an explicit ID; automatic name matching does
not merge ambiguous identities. Topic labels are canonicalized to lowercase,
with existing topic names supplied to the model for reuse.

Dates in reports are retained as occurrence descriptions (including relative
wording), alongside recording timestamps. Exact calendar normalization and
cross-conversation session continuation are not implemented. Progress history
can be retrieved across conversations; session pairing stays within one conversation.
Legacy kid events remain in the legacy review API and graph, and are no longer
injected into every chat. They are not automatically migrated or reinterpreted.

The JSON generation budget is 1536 output tokens to accommodate the reply and
structured records. Mocked integration tests verify the lookup/extraction
contract; live model wording and recognition still depend on model behavior.
