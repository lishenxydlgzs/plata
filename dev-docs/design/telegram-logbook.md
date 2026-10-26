# Telegram Log book tools

People have an explicit parent/child role; existing and automatically discovered
people remain unassigned. Only parents may be invited, approved or use Telegram.
Role writes use the trusted workspace API boundary. Demotion revokes the pairing,
pending invitations and queued delivery. Check the live role, pairing grant,
conversation and person at note-tool registration, every invocation and commit.
All confirmed parent accounts can read private Log book notes; no per-connection
note permission. Names and self-identification never confer roles.

Offer list_notes, read_note, create_note and update_note as native tools alongside
memory/music tools. Instructions make notes request-driven, not automatic event
substitution. Lists return IDs/titles/visibility plus trusted source IDs and directory;
reads return current note and quoted sources. No delete, restore or visibility tools.
New notes stay parents-only. Existing visibility remains unchanged. Ordinary search
and robot channels retain their existing privacy scope.

Use LogbookChat validation and revision application. Stage writes in request memory;
commit only after valid final JSON with a nonempty reply. Read before update and
check versions at commit. Quotations must match actual user messages in the same
Telegram conversation or sources of an explicitly read note. Copy those sources to
immutable journal entries only when committing. Preserve prior quoted material.
Record successful (pairing grant, Telegram update) results in the ontology database
in the same transaction as revisions so replay cannot duplicate notes. No tools are
replayed during JSON recovery; a failed final turn leaves no note writes.

Return sanitized errors; never log note bodies or quotations. Test grants/revocation,
forged contexts, private access defaults, source validation, revision conflicts,
atomic failure, replay, native recovery integration and workspace API protection.
