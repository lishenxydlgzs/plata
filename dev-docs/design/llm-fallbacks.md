# LLM Fallback Models

## Goal

Keep Plata responsive during temporary Gemini capacity or rate-limit failures without
changing its normal primary-model behavior.

## Model chain

The default ordered chain is:

1. `gemini-3.5-flash-lite` — normal primary model
2. `gemini-3.1-flash-lite` — first fallback
3. `gemini-2.5-flash-lite` — legacy final fallback

`GEMINI_MODELS` may override this with a comma-separated ordered list.

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
model, keeping the three-model chain within Home Assistant's request window.

## Verification

A unit test simulates a 503 from the primary model and verifies that the next model
is called and its structured response is returned.
