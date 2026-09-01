# Graph Review UI

## Goal

Provide a small, private web page that makes the current knowledge graph visible and
lets a parent direct its maintenance in natural language. This is an exploration and
refinement tool; it does not introduce a typed ontology or change the nightly job.

## Scope

- Serve a single-page UI from the existing agent-server at `GET /graph`.
- Display graph entities and links, ordered by most recently updated, with type
  filters, readable fact properties, and visible creation/update timestamps.
- Provide a maintenance chat panel. A parent can ask for the same operations as the
  nightly job: merge duplicate facts or improve a fact's display wording.
- Apply valid actions immediately because the parent initiated the request.
- Persist review sessions, messages, model replies, and applied actions in
  `conversations.db` so they can be inspected later.

## API

- `GET /api/graph` returns a read-only snapshot of all graph nodes and edges.
- `POST /api/graph/review-sessions` creates a session immediately before the first
  submitted chat message.
- `GET /api/graph/review-sessions` lists recent sessions.
- `GET /api/graph/review-sessions/{id}` returns its transcript and applied actions.
- `POST /api/graph/review-sessions/{id}/messages` stores a parent message, asks the
  maintenance model for a response and actions, applies valid actions, then stores
  the result.

The chat uses the graph snapshot plus the session's prior turns. The model can emit
only `merge` and `update` actions, using visible fact IDs. Invalid or unresolved IDs
are ignored and recorded as unapplied. The existing maintenance executor remains the
single implementation of those graph mutations.

## Persistence

`conversations.db` gains three tables:

- `graph_review_sessions`: session ID, title, creation and last-active timestamps.
- `graph_review_messages`: ordered user/model transcript rows.
- `graph_review_actions`: each proposed action, whether it was applied, and when.

The UI does not create a session when merely opened. It creates one only on the first
sent message, uses that message as the session title, and lists only sessions with a
transcript. This separates parent review history from child conversation history while
retaining both in the existing durable SQLite database directory.

## UI

The page uses plain HTML, CSS, and JavaScript—no new frontend build system. It shows
a searchable entity list and a link list, refreshes the graph after a successful
maintenance message and once each minute, and keeps the review panel visible while
the entity list scrolls. The graph uses a force-directed layout; users can drag a
node, pan the background, and zoom with the scroll wheel. The chat starts a session
only when the parent sends their first message and offers recent sessions for
retrospective inspection.

## Safety and limitations

This is intended for a trusted home network and uses the existing server's access
controls. It deliberately exposes only the nightly job's two action types. Structural
fact edits and arbitrary deletion are out of scope until the graph's behavior has
been observed in this UI.

## Verification

Tests cover graph snapshot serialization, creation/listing of persisted review
sessions, and a mocked model-driven review message that applies an update and records
the audit trail.
