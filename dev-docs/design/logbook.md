# Reflective household Log book

## Experience: chat operates on notes

The left panel is a read-only browser of past notes. The right panel is a persisted
conversation with a single composer and Send button. Selecting a note supplies
focus, without changing the conversation. There are no title/content forms and no
Save/Organize workflow. New conversation starts a fresh chat, not an empty note.

The model uses bounded JSON tool calls: list_notes, read_note, create_note,
update_note, delete_note, restore_note, and set_note_visibility. Read results return
to the model; writes are validated and committed together with the brief assistant
reply. Questions may produce a reply without any writes. Ambiguous edits/deletions
require clarification in chat. Delete hides a note and excludes it from retrieval;
its revision history and the original conversation remain recoverable.

journal_session and journal_message preserve conversations independently of notes.
Each note revision has original_quotes: [{message_id, text}]. The server verifies
that each quote is an exact substring of a stored user message made available to
the model, never an assistant message. At least one quote is required for a new or
updated note. Organized observations, author reflections, model suggestions, and
parking-lot questions remain separate. derived_from links preserve provenance.

A request ID makes each chat turn idempotent. The user message is saved before the
model runs; failure leaves it available for retry. All planned writes validate
before commit; stale notes cause a retry rather than overwriting another edit.
Existing note/entry/revision APIs remain compatible for saved records. Notes stay
parents-only unless the user explicitly asks to share them with family conversations.


## Ontology and retrieval

Notes use journal entities and immutable reflection_revision entities. Existing
journal_entry sources remain readable. Chat uses journal_session and journal_message
entities. has_entry, has_revision, and derived_from links retain provenance. Note
revisions can link existing person/topic/guidance_document entities through involves,
about, and interpreted_using. Unknown or ambiguous IDs are rejected.

Family conversation retrieval filters for current, nonarchived, family-visible
notes before ranking or constructing model context. Only reported observations and
author reflections are included; model suggestions, parking-lot questions, and raw
chat are excluded. Parent access uses the existing private management boundary,
not a new authenticated identity system. No automatic guidance changes, behavior
events, discipline assignments, or promotion of reports into verified facts.

## API and tool execution

GET/POST /api/logbook/sessions; GET /api/logbook/sessions/{id};
POST /api/logbook/sessions/{id}/messages with text, request_id, selected_note_id.
A selected note supplies context while the conversation remains independent.
The application dispatches validated JSON tool calls using the existing structured
LLM interface. Read results are passed back into the model. Up to four rounds and
five calls per round bound work. Write calls are validated as a batch and committed
atomically with the assistant response. All updates require a current read snapshot.

Each message supports 8,000 characters; a conversation supports 40,000 characters,
then a fresh conversation can continue working on existing notes. This is a bounded
initial implementation: the model gets the note index and may fetch individual
notes; semantic search and large-history compaction are future extensions.

## Verification

Use isolated synthetic data to test CRUD, provenance, invalid-quote rollback,
idempotent retries, stale edits, persistence, scoped retrieval, and existing graph
behavior. Browser checks cover chat-only operation, failure/reload/retry, exact
quotes, updates, deletion, graph navigation, and mobile overflow. Run backend and
ontology suites separately. Deploy server-only, then use real model calls with
clearly synthetic text and verify entities, revisions, and links in ontology.db.
Remove synthetic notes from active memory after validation. Never commit private
operational data or real family records.

## Validation result — 2026-09-20

- Agent-server suite: 63 passed. Ontology suite: 37 passed.
- Chrome desktop/mobile: no JavaScript errors or horizontal overflow; saved-message
  reload/retry, exact quotes, update, removal, retained conversation, and graph
  navigation verified with isolated synthetic data.
- Server-only deployment passed health checks.
- Real LLM calls created and updated a synthetic note with exact source quotes and
  existing ontology associations, then deleted it through the note tool.
- Direct operational DB inspection confirmed the journal, two revision rows, three
  derived_from links before cleanup, and six conversation message rows preserved
  after deletion. Parent-only exclusion, family inclusion, and deleted-note
  exclusion passed. Live testing exposed a singular/plural retrieval mismatch;
  it was fixed, regression-tested, redeployed, and verified.
- Synthetic notes were archived and the synthetic person was removed. No private
  household content is included in this report or in committed test fixtures.

## Conversational reflection — 2026-09-20

Chat is an ongoing reflection, not a sequence of finished submissions. The assistant
may acknowledge, explore an observation, or ask one useful follow-up without writing
a note. When enough useful material emerges it may create a partial note, then
incorporate later reflection into the same note. No per-message write requirement
and no completion/save ritual. Replies address the parent's thoughts; tool receipts
are secondary. Original quotes may span several user turns, while assistant
suggestions remain clearly separate. Do not infer the parent's reflection from a
bare observation or repeatedly ask questions when they simply want to continue.
