# Conversation history mixed separate sessions

Status: Resolved locally (2026-09-10).

ConversationDB.get_history accepted a conversation ID but queried the latest
messages globally. This could mix unrelated children's lessons in the prompt.
The query now filters by conversation_id and uses message IDs for deterministic
ordering, including messages recorded during the same second.

Validation: test_conversation_history_is_scoped_and_ordered, with interleaved
messages from two conversations. Long-term learning history is retrieved
separately using explicit person IDs and topics.
