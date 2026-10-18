# Ontology-owned media catalog

## Ownership and model

The ontology owns media identity, titles, classification (including pending/error,
last-good metadata, provenance and fingerprints), file availability, playlists,
and ordered `contains` links. Audio bytes remain on disk. Import cadence/history
remain in the jobs database; that database does not own music metadata.

A media entity is identified internally by its ontology UUID, with a unique
`media_path` identifier for its relative local file path. A playlist also uses its
UUID, retaining `playlist_id` identifiers as compatibility aliases. Source folder
playlists carry folder/import configuration. Derived CC playlists are materialized
views of persisted classifications, rebuilt transactionally with membership and
positions. One file has one entity across all views. Missing files keep their
entities and history but cannot execute. Legacy CC backup files are retained but
hidden after cutover. Other/custom graph records and user links are preserved.

Search, playlist resolution, progress context, import review, and playback all
read the same repository over ontology entities/links. No live manifest catalog,
filesystem catalog cache, or separate song-ID index remains in those paths.
Filesystem reconciliation discovers new files and updates availability only; it
must not overwrite database metadata on subsequent scans.

## Migration and compatibility

Before changing readers, reconcile existing file paths with existing media entity
properties and aliases, reusing UUIDs. Import manifests once in the same database
transaction as a version marker. Preserve classification failures and successful
fingerprints, source configuration, existing playlist UUIDs, ordered membership,
and every playback session. If the media mount is absent, do not mark manifest
migration complete; retry when the files become accessible. Leave original manifests untouched for rollback; after
migration they are inert backups and cannot override graph changes on restart.
A failed migration rolls back its marker and all graph changes for safe retry.

Old filename and playlist IDs remain accepted only at the resolution boundary.
The earlier local `song:<path hash>` IDs can also resolve there for conversation
compatibility; new tool results and progress context expose UUIDs only. Existing
session playlist aliases are migrated to UUIDs; their file-based progress remains
unchanged. Session cursor reconciliation continues to preserve completed files
when a playlist changes; no position or completion state is reset by migration.

## Import and classification

Publish a downloaded file and its source metadata into the ontology immediately
after atomic filesystem publication. Existing downloads are reused. Persist a
pending state before model calls and save each validated batch transactionally,
without holding a database transaction across awaits. Failed or interrupted
classification is retried next run without redownloading; last-good metadata stays
usable. Recompute group membership after each publication/classification batch.
Persist catalog updates even when later videos or classification fail.

## Agent contract

`search_media` reads media/playlist entities and ordered membership with bounded
literal text search, kind, subject, cycle, week, tag, availability, exact entity-ID,
and playlist-membership filters. Playlist metadata filters match a member song
satisfying all requested filters. Membership browsing preserves playlist order;
other results sort deterministically and paginate. Missing members remain in
ordered membership and are visible with availability filters. Search returns total
and available track counts; execution rejects a playlist with unavailable members
rather than silently skipping them. Results distinguish song and
playlist, expose UUIDs and bounded useful metadata/counts, and never expose paths
or arbitrary graph properties. The agent chooses queries and selections. No intent
routing, automatic playlist substitution, or title-derived syllabus rules.

Playback validates selected entities and file paths against the repository and
current filesystem, then emits the existing HA actions. Missing/unavailable songs
cannot become playback actions. Whole-playlist playback keeps ordered membership
and persistent progress; selecting a song directly uses its same entity UUID.

## Verification and rollout

Test migration/restart/idempotence/rollback, graph edits surviving reconciliation,
missing/restored/hidden files, stable identities, bounded search/filter/pagination,
classification failure/retry/caching without another download, membership order,
old-ID compatibility, direct song execution, progress after migration and catalog
changes, and channel restrictions. Tests use synthetic data and stub model calls.
Full deployment is gated on reconciling the documented production checkout drift;
implementation and local verification do not require deployment.
