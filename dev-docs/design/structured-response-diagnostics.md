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
