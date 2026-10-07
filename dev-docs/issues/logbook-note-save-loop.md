# Logbook note deferral and failed save loops

Status: resolved and verified in production, 2026-10-06.

## Confirmed cause

Initial production logs showed repeated failures at the four-call model limit,
with no tool-level diagnostics. Adding content-free run diagnostics and retrying
the pending request exposed three rejected staging calls followed by a successful
standalone read, leaving no model budget to finish the save.

The native `stage_note_changes` schema reused the legacy `ToolCall` enum, which
advertised `read_note` and `list_notes` as batch operations. The staging executor
correctly rejects reads in a write batch. The model followed the misleading
schema and repeatedly submitted read operations in staging batches. The round
limit was a consequence, not the underlying contract defect.

After excluding read operations, the original pending save completed. Reviewing
the note showed that it omitted the initial observation. A follow-up to include
that material exposed a second contract gap: generic `arguments: dict` left note
fields and quote structure unspecified in the tool schema. Missing fields and
malformed quotes consumed correction rounds; staging succeeded only on the final
allowed call, leaving no budget for the reply and atomic commit.

Separately, the initial observation received a conversational reply without note
changes. The prompt permitted deferral while gathering context and lacked a
strong default to capture a substantive first observation.

## Repair

- Give native staging a write-only schema; retain standalone read tools and the
  four-call limit, domain validation, atomic commits, and request idempotency.
- Expose typed nested note arguments, including required content fields and quote
  objects, in the native schema; preserve operation-specific domain validation.
- Clarify read-before-stage sequencing, finish-after-stage behavior, source IDs
  versus association IDs, and same-turn capture of substantive observations.
- Log correlated run/request/session IDs, tool outcomes, safe validation reasons,
  argument structure, timing, staging, commit, replay, and unsuccessful completion.
  Do not log private note text, tool argument values, or raw exceptions.
- Preserve pending messages and retry through the normal application endpoint.

## Validation

148 backend tests pass, including SDK argument failures, quote rejection/recovery,
round exhaustion, diagnostic privacy, and a regression for a mixed read/write
batch corrected within four model rounds, plus malformed content recovery and
delete/restore/sharing conversion. Browser type checking, five browser tests,
production build, dependency consistency, and server health passed deployment.

The original pending request completed through the native HTTP stream. A follow-up
and retry saved the initial observation in its relevant existing note, with a
validated quote from the first message. Both note updates have persisted revisions
and the conversation has no pending message. The final run had no structural
schema failures; it corrected an exact-quote mismatch and committed within four
model calls. Diagnostics recorded both that rejection and the successful commit.

## Note identity follow-up

The recovery initially updated two existing notes instead of creating the new
note the user expected. Successful commits alone did not establish that the
requested organization was correct. After the user clarified, a separate private
note was created through the normal agent endpoint with verified quotations from
both initial observations. The active note count increased from two to three,
and the corrective request left existing notes unchanged. The shared prompt now
defaults new conversations to new notes and later turns to that conversation's
relevant note. Updating an older note requires a clear request or clear continuation;
topic similarity and selection alone are insufficient. This is a prompt-only
improvement, with no new context fields or deterministic routing enforcement.
