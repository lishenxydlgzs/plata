# Shared job management

Add `/jobs`, linked from the household workspace, to manage knowledge-graph quality
maintenance and YouTube playlist imports. Show enabled state, cadence, next/last
run, and outcome. Support editing intervals in hours (1–8760), pausing/resuming,
queuing a run, and creating playlist jobs. Preserve the maintenance job's initial
midnight server-local start, then repeat at its saved interval (24 hours by default).
Times are displayed in the browser's local timezone. Interval edits schedule the
next run one new interval from now; Run now is explicit and independent.

Use one worker for both types to prevent overlap. Persist maintenance configuration
alongside existing playlist jobs without changing playlist IDs or folders. Preserve
existing playlist APIs, filtering out maintenance. Route the legacy maintenance
run endpoint through the same tracked runner. Stop using the old maintenance timer.

Store executions and bounded, task-scoped logs in the existing private SQLite file.
Record trigger, start/end, running/succeeded/failed/interrupted state, summary,
and error. Capture only job module logs in the active async context, excluding
unrelated conversation logs. Retain the newest 100 runs per job and at most 500 log
records per run, each at most 2000 characters. Persist progress during execution.
Mark unfinished executions interrupted on restart, and requeue interrupted jobs.
Failures in the maintenance LLM must surface as failed runs, not successful no-ops.

History APIs paginate by run ID; the UI shows recent runs and loads older pages.
Render data as text, with clear loading, empty and error states; polling must not
replace unsaved schedule edits. Existing trusted household-network access applies.
No historical logs can be reconstructed. Tests use disposable databases and fake
job handlers; never call Gemini or download actual videos during verification.

## Shared navigation

All workspace pages use one server-rendered navigation partial and stylesheet,
with Log book, Graph, and Jobs in the same header. Hash links select the journal
or graph view, including direct links and browser back/forward navigation. The
active view is marked with `aria-current="page"`. Page content may have different
layouts, but header styling and outer content spacing remain shared.
