# LLM Fallback Models

## Goal

Keep Plata responsive during temporary Gemini capacity or rate-limit failures without
changing its normal primary-model behavior.

## Model chain

The default ordered chain is:

1. `gemini-3.5-flash-lite` — normal primary model
2. `gemini-3.1-flash-lite` — first fallback
3. `gemini-3.6-flash` — additional Flash fallback
4. `gemini-3.7-flash` — requested compatibility alias (Google routes it to 3.8)
5. `gemini-3.8-flash` — final Flash fallback

`GEMINI_MODELS` may override this with a comma-separated ordered list. The deployed
API key's model listing includes all three added IDs and `generateContent` support;
listing does not prove generation quota or billing access. No live generation calls
are needed to change the configuration. Google's 3.7 documentation notes that
3.7 requests route to 3.8, so these entries do not offer independent capacity:
https://ai.google.dev/gemini-api/docs/models/gemini-3.7-flash

## Behavior

Each generation request starts with the first configured model. On temporary HTTP
failures—429, 500, 502, 503, or 504—or a model-specific 404, the request is retried
with the next model. This lets Plata skip a configured model that is retired or not
enabled for the account. Authentication errors, invalid requests, malformed output,
and other non-temporary failures are returned immediately. The existing
child-friendly fallback reply remains the final behavior when every model fails.

The shared fallback helper is used by conversation generation, structured chat
generation, simple JSON generation, and nightly graph maintenance.

Each model attempt has an eight-second application timeout by default, configurable
with `GEMINI_MODEL_TIMEOUT_SECONDS`. A timeout advances immediately to the next
model. Longer configured chains can exceed Home Assistant's request window when
multiple attempts time out; streaming agents use their existing per-call timeouts.

## Verification

A unit test simulates a 503 from the primary model and verifies that the next model
is called and its structured response is returned.
