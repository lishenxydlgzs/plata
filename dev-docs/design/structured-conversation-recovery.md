# Structured conversation recovery

Conversation turns use native tools. Gemini JSON mode is disabled
while tools are offered, so a final prose response can fail JSON parsing before
validated learning/behavior events are stored.

If a tool-enabled turn returns invalid JSON or a non-object value, make one
tool-free continuation using JSON mode. Preserve the original prompt and complete
agent transcript, including search results. Ask the model to produce the documented
response object from that context. Do not wrap prose in a success response, infer
intent in code, or replay tools. Validate the recovered object normally; on another
failure, retain the existing fallback and perform no domain writes. No recovery is
attempted for tool execution errors or model transport failures.

Authorized channel tools may already have queued durable deliveries before the
final response failed. Recovery does not undo or replay those actions. It preserves
their results in the transcript. Keep main's schema-based classification support,
explicit classification-only excerpt logging and agent-controlled turn completion.

This fixes output formatting, not channel authorization or note capabilities.
Telegram event extraction remains distinct from parent-authored Log book notes.
Creating or updating those notes from Telegram needs a separate design covering
parent authorization, source quotations, note visibility, and durable idempotency.

Verify with synthetic provider responses: prose after search recovers with tools
absent and JSON mode enabled; context is preserved; invalid recovery fails closed;
valid JSON uses no additional model call.
