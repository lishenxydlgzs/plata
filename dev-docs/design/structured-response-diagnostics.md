# Structured response diagnostics

Failed song classification must leave enough evidence in persistent job logs to
distinguish malformed JSON, a non-object response, schema validation errors, and
incorrect batch membership. Keep existing retry and last-good metadata behavior.

The shared runtime logs model ID, stop reason, response length and parsing/type
errors without prompts or conversation history. An explicit classification-only
option enables a JSON-escaped response excerpt capped at 1,000 characters.
Classification schema and membership failures log bounded details and the response
excerpt. Attach the runtime logger to task-local execution history so diagnostics
appear in View logs as well as server logs. Do not log successful response bodies.

## Conversation recovery diagnostics

Assign each structured turn a random diagnostic ID, shared by its initial run,
provider fallback attempts, and optional JSON recovery. Log model attempt number,
model ID, phase, duration, configured output limit and timeout, JSON mode, provider
stop reason and token usage. Missing provider metadata remains null, not zero.
Record transport failure class without exception text. Report the final parse
outcome with response length, a fixed response-shape category, JSON error position
and error class. Record recovery success or failure under the same ID.

Conversation diagnostics never include prompts, response excerpts, tool arguments/results, conversation IDs,
names, credentials, or arbitrary exception strings in these diagnostics. Shape
categories distinguish empty output, fenced output, object/array starts and other
text without recording content. Token counts are provider-reported per model call;
they are not inferred from character counts or summed across tool rounds.

Use synthetic provider tests for max-token truncation, invalid prose and recovery,
transport fallback, missing metadata, correlation and private-content exclusion.
Existing event persistence and model fallback policy remain unchanged.
