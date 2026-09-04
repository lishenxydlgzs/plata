# LLM Fallback Models

## Goal

Keep Plata responsive during temporary Gemini capacity or rate-limit failures without
changing its normal primary-model behavior.

## Model chain

The default ordered chain is:

1. `gemini-3.1-flash-lite` — normal primary model
2. `gemini-2.5-flash-lite` — low-cost, low-latency fallback
3. `gemini-2.5-flash` — capable final fallback

`GEMINI_MODELS` may override this with a comma-separated ordered list.

## Behavior

Each generation request starts with the first configured model. On only temporary
HTTP failures—429, 500, 502, 503, or 504—the request is retried with the next model.
Authentication errors, invalid requests, malformed output, and other non-temporary
failures are returned immediately without trying another model. The existing
child-friendly fallback reply remains the final behavior when every model fails.

The shared fallback helper is used by conversation generation, structured chat
generation, simple JSON generation, and nightly graph maintenance.

## Verification

A unit test simulates a 503 from the primary model and verifies that the next model
is called and its structured response is returned.
