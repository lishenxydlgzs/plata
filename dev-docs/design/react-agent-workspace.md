# React agent workspace

Status: implementation in progress. Production rollout is separate.

## Objective and existing system

Replace the hand-written browser screens with a cohesive React workspace using
CopilotKit conversations, AG-UI streaming, and Python Strands orchestration.
Retain FastAPI, the existing domain services, and both SQLite stores. Do not
copy production data into development or introduce a second source of truth.

The initial audit found three browser areas: logbook and graph review share
`/graph` with hash navigation; jobs live at `/jobs`. Logbook supports persisted
conversations, pending-message retries, selected notes, original quotations,
revision history, and graph connections. Graph review supports visualization,
search/type filters, persisted maintenance conversations, record corrections,
and action outcomes. Jobs support playlist imports, schedule/pause/run controls,
paginated execution history, and execution logs. Household guidance, people,
learning/behavior events, review updates, and independent memory settings have
existing REST APIs and need dedicated screens in the new workspace.

The voice router retrieves history by conversation ID, delegates to ChatHandler,
and persists the reply. ChatHandler prepares memory/guidance/media context,
optionally retrieves household history, validates device actions, and records
facts/events. The Gemini helper supplies ordered model fallback and timeouts.
LogbookChat currently runs a bounded JSON tool loop with atomic validation before
writes; GraphReviewService validates corrections and records action outcomes.
These domain invariants must survive the orchestration change.

Baseline: 85 agent-server tests pass before implementation (2026-09-21).

## Architecture

- `packages/web-ui`: React + TypeScript + Vite application, with CopilotKit and
  AG-UI client. Prefer direct self-hosted agent connections and static assets;
  verify installed CopilotKit APIs before deciding whether a Node runtime is needed.
- FastAPI serves built browser assets and existing REST resources on port 8200.
  Existing `/graph` and `/jobs` entry points retain useful navigation behavior.
- Browser conversations use server-owned session IDs and AG-UI event streams.
  Load authoritative history from SQLite, never trust client-supplied assistant
  messages or tools as permission to change records. Reject unsupported inputs.
- Strands owns model/tool orchestration. Tools call the existing validation and
  persistence services. Keep quotation checks, revision snapshots, visibility
  checks, action allowlists, and all provenance links in those services.
- Gemini remains the provider, using `GOOGLE_AI_STUDIO_API_KEY`, `GEMINI_MODELS`,
  and `GEMINI_MODEL_TIMEOUT_SECONDS`. No AWS or OpenAI account is required.
  Retry eligible model errors without replaying already committed mutations.
- `/conversation`, hardware and playback contracts remain stable. Move voice
  generation behind Strands while preserving bounded output and domain validation.
- Streaming must represent actual agent progress and results. Never report a
  mutation as successful before commit. Reconnect/retry must not duplicate writes.

## Compatibility evidence and verification

Official Strands documentation describes the native `GeminiModel` provider,
including streaming and structured output:
https://strandsagents.com/docs/user-guide/sdk/model-providers/google/

CopilotKit documents the Python Strands integration through AG-UI:
https://docs.copilotkit.ai/strands/quickstart

The upstream adapter source is at:
https://github.com/ag-ui-protocol/ag-ui/tree/main/integrations/aws-strands/python

Public registry inspection found Strands 1.56.0, ag-ui-strands 0.4.0, and
CopilotKit 1.73.0. Published package APIs and dependency requirements must be
tested together locally before treating these as the supported combination.
The local interpreter is Python 3.12; keep the robot's Python requirement explicit.
Use mocked Gemini transport for streaming, tool, fallback, and failure tests;
do not spend the free-tier quota on repeated conversational smoke tests.

## Product design

Use a calm, warm workspace with a narrow persistent sidebar, spacious paper-like
content cards, restrained teal accents, readable prose, and consistent controls.
Primary sections: Logbook, Knowledge, Household, Jobs. Conversations sit beside
the record being reviewed on desktop and below it on small screens. Provide
visible keyboard focus, semantic labels, sufficient contrast, accessible status
messages, and deliberate empty/loading/error states. Avoid exposing raw JSON as
the primary record presentation; provenance and IDs belong in detail disclosures.

## Delivery stages

1. Document audit and verify dependencies; implement one real logbook workflow
   through React, CopilotKit, AG-UI, Strands, and existing persistence.
2. Complete logbook tools, retries, note history, and connection navigation.
3. Migrate graph review and add household management screens; preserve filters,
   correction provenance, persisted chat, and action results.
4. Migrate jobs/playlist management including history pagination and logs.
5. Migrate voice generation to Strands with existing HA contract regression tests.
6. Build/deployment integration, browser checks, full regression verification,
   and documented setup/rollback. Do not deploy to production during this goal.

## Completion evidence

Track all requested capabilities in requirements.md. Require backend regressions,
frontend type checks/tests/build, mocked end-to-end AG-UI tool/persistence tests,
and actual desktop/mobile browser inspection with synthetic data. Verify failure
and retry behavior, conversation isolation, fallback, source quotations, privacy,
and unchanged media/timer responses. Deployment must build assets before sync,
preserve operational data, and document restoring the prior application release.
An attractive static mockup or a stream wrapping the old JSON loop is not completion.

## Graph retry persistence decision

Browser graph-review turns will use an additive receipt table in ontology.db,
keyed by session and client message ID. Keep existing session identities and
legacy review history in conversations.db; the session API combines both for
readers. A synchronous ontology transaction commits validated graph mutations,
action outcomes, and the assistant receipt together. Retry returns the stored
receipt instead of invoking tools again. The store's existing eager commits need
a scoped transaction helper so maintenance operations can participate in that
atomic batch. Pending user messages survive provider failure and process restart.
No existing records are rewritten or discarded.

## Verification result — 2026-09-24

Implemented the migration without deploying to the robot. Supported versions are
pinned in the Python project and npm lockfile. The frontend uses CopilotKit's
self-managed `HttpAgent` directly against FastAPI; it needs no separate runtime
server. Strands runs both browser tools and the existing structured voice turns.

- Agent-server: 106 tests passed, including real Strands/AG-UI streaming, native
  Gemini function-call conversion with mocked transport, atomic graph changes,
  note reading/revisions, verbatim quotes, server-owned context, replay receipts,
  SDK tool validation failures, cancellation, fallback and HA contracts.
- Ontology: 38 tests passed, including nested transaction commit and rollback.
- Frontend: 5 API/error/ID tests passed; TypeScript and Vite production build
  passed. `pip check`, shell syntax checks and `git diff --check` passed.
- Browser: inspected 1440×1000 desktop and 390×844 mobile layouts using disposable
  data. Verified note creation and reload, failed logbook message retry, graph
  filtering and correction with an applied receipt, guidance/person creation,
  job pause/resume/save/manual queue, playlist addition, older history and logs.
  The preview used actual HTTP, persistence, tools and adapter with a synthetic
  model; workers were disabled and execution-history records were fixtures.
- Fixed issues found during verification: SQLite tools now execute asynchronously
  on the server loop; partial mutation batches roll back; replies claiming writes
  wait for commit; retry IDs match stored requests; assistant text is readable
  with a dark OS preference; all navigation sections fit narrow screens; and
  polling preserves history pagination and unsaved schedule edits.

No live Gemini calls, playlist downloads, production data migrations or robot
restarts were performed. The remaining rollout checks are real-provider latency,
voice-device smoke testing and Pi performance. Vite reports a large CopilotKit
bundle (approximately 633 KB gzip for the main JavaScript chunk). Setup, deployment
and application rollback are documented in [workspace operations](workspace-operations.md).
