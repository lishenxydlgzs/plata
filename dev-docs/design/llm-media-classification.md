# LLM media classification and graph associations

Replace the CC title keyword parser with an agent classification pass. The model
interprets public video titles and playlist context; code validates structured
output and persists it. No audio transcription or private household graph data is
sent for classification. Source titles are untrusted data, never instructions.

Return canonical playback subjects, explicit cycles (or all-cycles applicability),
weeks, free-form tags/topics, and named learning entities with a kind such as
concept, place, historical person, event, or work. Include confidence and a short
explanation. The prompt explains CC conventions, including that no cycle in a
title means applicability to every cycle, and asks the model to leave unsupported
weeks/topics unresolved rather than inventing curriculum facts. Code checks
types, bounds, vocabulary shape, and exact input/output video-ID correspondence;
it does not infer classifications or fall back to keyword matching.

Use the existing Strands/Gemini runtime with small batches and a minimum ten
seconds between batch starts to leave room under the 15 RPM quota. Persist a
classifier-version/input hash, timestamp, source inputs, and model configuration
alongside each validated result. Reclassify new/changed titles, changed playlist
context, and legacy rule-derived entries. Unchanged successful entries cost no
model calls. Include bounded prior classification vocabulary for consistent names.
Publish valid batches atomically; on failure retain last-good classifications,
mark affected tracks pending/error, continue other work, and retry next sync.
Downloaded audio remains available; unclassified new songs get no invented groups.

Sync graph associations once per unique media file. Reuse named topic entities;
use separate curriculum subject/cycle/week, media-tag, and learning-entity types
for structured associations, never household person entities. Link through a
dedicated classifier-owned relationship with classification provenance. Reconcile
only these links when metadata changes, preserving user/conversation associations.
Keep subject/week playlists as views over validated model metadata. Expose tags,
topics, named entities, confidence, explanation, and retry state in Jobs. Make
tag/topic/entity views discoverable to the playback agent for thematic requests.

Migrate the existing curated library during the next sync without downloading
audio again. Existing catalog visibility, backup retention, and saved playback
progress remain intact. Test batching, caching, title changes, invalid/partial
model responses, failure retention/retry, graph idempotence/link reconciliation,
and catalog/UI behavior with synthetic data and mocked model calls.
