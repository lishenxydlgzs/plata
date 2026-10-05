# Curated CC playlist catalog

CC organization uses LLM classification of public titles and playlist context.
The agent assigns subjects, cycles, weeks, tags, topics, and named learning entities;
code validates and persists those choices. Titles without a cycle apply to every
cycle. See [LLM media classification](llm-media-classification.md) for batching,
caching, uncertainty, and graph associations. Legacy title-rule metadata is
reclassified on the next sync without downloading existing audio again.

Keep one audio copy per source video in the existing playlist folder. Atomically
persist a hidden JSON manifest containing original titles and derived metadata.
Reclassify changed or uncached metadata on sync without downloading audio again. Keep removed
videos locally, matching the existing additive import contract.

The media scanner exposes virtual CC playlists by subject, cycle, and cycle/week,
including subject subsets and an unclassified group. Preserve the existing
`cc_cycle3_week_5` naming convention. Only advertise nonempty groups. Playback
uses manifest metadata instead of guessing subjects from paths for these tracks.

An explicit “Replace old CC catalog” setting hides legacy `cc_cycle1/`,
`cc_cycle2/`, and `cc_cycle3/` trees once a CC import finishes successfully with
playable tracks. Keep old files as backups. Failed/empty initial imports do not
hide them. Successful replacement remains active during later failed imports.
Disabling replacement takes effect on the next sync. Other media remains visible.
The catalog is the playback authority; historical ontology records remain for
provenance. Refresh ontology catalogs after imports and reject stale resume
cursors when a playlist is replaced. When tracks are added, preserve completed
file identities and queue unheard tracks instead of resetting progress.

Expose CC settings on create/update APIs and the Jobs page, and a tracks endpoint
and catalog review section showing title, subject, cycle, and weeks. Migrate
existing job rows with CC mode disabled. Keep cadence-only updates compatible.

Validate with mocked downloads: incremental additions, title edits, classifications,
uncertain titles, persistence, failed replacement, retained backups, API changes,
and playback through generated groups. Do not commit operational playlist data.

## CC playback requests and progression

Playback interpretation belongs to the LLM. Supply available concrete playlist
IDs, source-cycle metadata, and recent per-playlist progress (including completed
sessions, remaining song counts, and next unfinished titles) as facts. Instructions
explain the usual expectations for weekly play, continuing an unfinished week,
advancing a finished week, and starting over, while allowing conversational context
and explicit user requests to override those defaults. The model may ask a question
when the cycle or desired scope is unclear; backend code does not choose a cycle,
advance a week, wrap a sequence, or generate a replacement spoken reply.

The model selects concrete `media_ids` and `media_operation` (`play`, `resume`,
`restart`, or `stop`). To reset a broader sequence, it explicitly lists the concrete
`reset_playlist_ids` with a restart request. Validate IDs against the current
catalog; never infer reset scope from CC prefixes. All language, including stop
requests and negations, passes through the model.

The backend retains execution mechanics: persist progress, reconcile completed
file identities after catalog changes, resume the selected playlist, supersede
explicitly reset sessions, and perform HA actions. Resuming a finished playlist
is a no-op, not an automatic move to another playlist. Progress is per song;
interrupted songs resume at their beginning. HA completion callbacks remain the
source of truth. Tests use mocked model choices to verify the executor follows
them, preserves model replies, and supplies accurate context without imposing
CC-specific interpretation rules.
