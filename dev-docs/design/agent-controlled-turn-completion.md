# Agent-controlled turn completion

Remove the four-model-round cap so an agent can search, invoke tools, and produce
its final reply without a fixed number of reasoning cycles. Apply this consistently
to robot/Telegram structured turns, browser logbook and graph agents, and the
legacy logbook read/response loop. Remove prompt guidance that requires stopping
after four rounds and the obsolete round-limit diagnostic classification.

The model ends the turn by returning its final response. Preserve per-model-call
timeouts, existing browser run deadlines and cancellation, bounded retrieval
results/search-call budgets, schema validation, staged-write checks, account
permissions, and durable delivery. These constrain individual mechanisms rather
than deciding when the conversation has used enough model rounds.

Verify a structured conversation and a streaming logbook run can use more than
four tool rounds and then return a final response. Preserve failure diagnostics
for actual model errors and run the backend regressions. No deployment is implied
by this change.
