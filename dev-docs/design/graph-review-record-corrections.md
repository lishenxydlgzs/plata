# Review errors and record corrections

The graph review UI currently assumes every failure is JSON, while upstream
model errors escape as plain HTTP 500 responses. Its assistant sees only facts
and topics, so it cannot locate or correct messages and household events.

Return a structured, retryable 503 for failed or malformed model responses before
applying actions. Render non-JSON HTTP failures safely in the UI. Preserve review
history without sending the current user message twice to the model.

Supply bounded, relevant messages, legacy kid events, behavior events, learning
events and people alongside existing facts. Add a Review button on graph records
so older records can be targeted explicitly. The assistant must disambiguate when
a report is not uniquely identifiable; quoted content alone is not edit permission.

Allow an explicit correct_record action for messages and events, with corrected
text and optional existing person ID for events. Preserve original transcript,
source links and an append-only correction audit tied to the review session and
user request. Changing an event's person updates its involves link and child_name;
it clears learning session membership to avoid retaining another child's session.
Other structured fields and guidance revisions cannot be edited through this
operation. Corrections to a message do not silently rewrite derived records.

Test upstream failures, invalid actions, ambiguous IDs, provenance preservation,
person relinking, current-turn history, and frontend plain-text HTTP errors using
synthetic records only. Deploy only the changed backend files.
