# Telegram response formatting after memory search

Status: resolved — integrated with current main; regression coverage added.

A tool-enabled conversation can end in prose rather than the required JSON object.
Parsing then fails before event persistence and the caller receives the generic
fallback. JSON mode is disabled while Gemini is offered tools. Existing transport
fallback does not recover output-format failures.

The runtime now makes one tool-free JSON continuation with the complete transcript
on invalid final output. Tools are not replayed, and a second invalid response fails
closed. Synthetic provider regressions cover context preservation, absent tools in
recovery, bounded failure, and no events saved on failure.

Diagnostics now correlate the initial turn, model attempts and JSON recovery with
a random ID. Each attempt reports phase, model, stop reason, token usage, output
limit, JSON mode, timeout, duration and error class. Parse outcomes include response
length, a content-free shape category and JSON error position. No response excerpts,
prompts, tool payloads or arbitrary exception messages are added to these logs.
Synthetic tests distinguish SDK max-token exceptions from final parse failures and
verify recovery success/failure, transport fallback and private-text exclusion.

Parent-authored Log book notes are now supported separately through native note
tools for confirmed parents. People roles govern Telegram access; source-backed
note writes are staged until valid final output and committed idempotently.
See ../design/telegram-logbook.md.

Reconciled with current main, preserving schema-based classification, explicit
classification diagnostics, Telegram music delivery and agent-controlled turn
completion. Compared all 111 tracked application/deployment file hashes against
production before integration: no production-only differences. Deploy through the
primary checkout on main using the normal backup and rollback workflow.
