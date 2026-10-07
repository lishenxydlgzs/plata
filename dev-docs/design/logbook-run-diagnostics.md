# Logbook run diagnostics

Add structured events to the existing rotating server log for each logbook run:
start, model round, tool completion, staging, commit, replay, and unsuccessful
termination. Correlate events by session, request, and run IDs. Include tool name,
duration, round count, staged state, and write count so a failed save can be traced
without logging private conversations, note bodies, tool arguments, or results.

Inspect SDK `AfterToolCallEvent.exception` to capture failures before tool bodies
execute as well as domain validation errors. Record allowlisted domain reason
codes and Pydantic error types/field paths without values or exception messages.
Unknown errors retain their exception class; do not serialize arbitrary errors.
Logs are diagnostic only and must not choose tools or alter commit semantics.

Validate using synthetic model/tool events, including quote rejection, SDK input
validation, retry recovery, round exhaustion, and no-write success. Deploy the
agent server, retry the existing pending message through its normal HTTP stream
using the saved request ID, and verify the persisted receipt and note revision.
Use the resulting evidence to fix the failing tool contract if needed. Never
replay a completed request as a new turn or bypass normal note validation.

## Observed failure and repair

The instrumented retry repeatedly sent read operations inside the write batch,
which exhausted the model budget before a write could be staged. The native
staging schema reused the legacy tool-call enum, advertising reads that the
executor rejects. Introduce a write-only native call model and explicitly direct
reads to standalone native tools. Preserve the shared validator, atomic commit,
and four-call budget. Clarify source-message IDs versus association-directory IDs
and the instruction to capture substantive observations in their initial turn.
Tests must verify the provider-facing schema excludes reads and that a malformed
batch can be corrected into a standalone read and valid write without side effects.

A subsequent content-completion attempt revealed missing note fields and quotes
submitted in the wrong structure. The generic `arguments: dict` hides the nested
contract from the tool schema. Add typed native arguments for note ID, content,
and visibility, including the full `NoteContent` schema. Omit absent optional
arguments when converting to the existing domain call; `_validate` continues to
enforce the exact required arguments for the selected operation. This preserves
agent choice while making its input contract available to the model/provider.

## Reading diagnostics

On the robot, filter `/home/lishenxydlgzs/logs/agent-server/agent-server.log`
(and its dated rotations) for `logbook_run`. Each matching line ends in JSON.
Group by `run_id` for an attempt or `request_id` for all retries of one message.

- `tool_completed` with `status: error` carries an allowlisted reason and,
  for schema failures, field paths and validation types. Input shape contains
  field names/types only; unfamiliar names are redacted.
- `changes_staged` is not a save receipt. Only `commit_succeeded` confirms durable
  completion; `writes: 0` means a conversational response without note changes.
- `agent_error` with `reason: round_limit` records model-budget exhaustion.
  Inspect earlier tool failures in that run to find the cause.
- `run_finished` records whether commit happened even when the stream fails or
  closes early. `request_replayed` means an already-completed request was returned
  without another model call or write.

An unclassified error records its exception class without arbitrary exception
text. Add narrowly defined reason codes when new reproducible failures appear;
do not enable raw prompt or tool-result logging to diagnose family notes.
