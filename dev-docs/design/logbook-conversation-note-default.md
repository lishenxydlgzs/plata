# Conversation-scoped note default

Update only the shared logbook behavioral prompt, inherited by both the legacy
chat endpoint and the native streaming agent. When useful material first emerges
in a new conversation, default to creating a new note even if older notes cover
similar topics. Later observations and reflections in that conversation normally
extend its relevant note rather than generating a note per message.

Updating a note from another conversation requires a clear user request or clear
continuation of that particular note. Topic similarity or selecting a note alone
does not establish that intent. Honor explicit requests for a separate note or
for no note writes, and clarify an ambiguous requested update target.

This is an agent instruction, not an executor constraint. No new context fields,
database changes, routing logic, or tool changes are introduced. Run existing
backend tests and normal deployment checks; those verify compatibility, not
deterministic model adherence to the new default.
