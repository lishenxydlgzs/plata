# Review UI errors and missing event context

Status: Resolved (2026-09-11).

The browser parsed every HTTP error as JSON. Upstream generation/JSON failures
escaped from review as plain HTTP 500, producing an unhelpful browser parsing
error. Separately, review only supplied facts/topics to the model, excluding
messages and household events that parents expected to inspect or correct.

Review now returns a structured 503 for unusable model responses before actions
start; the browser also handles non-JSON failures and retains the draft for retry.
Review context includes bounded relevant records and supports explicit record IDs.
Message/event corrections preserve source text and append a correction audit;
existing people can be reassigned on events with matching graph links.

Validation: backend regression suite and a synthetic browser test covering record
selection, plain-text HTTP 500, draft retention, and a successful retry. No private
operational records are used as fixtures or altered by these checks.
