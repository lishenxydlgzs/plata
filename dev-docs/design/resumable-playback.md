# Resumable Playlist Playback

## Goal

Allow Plata to stop one playlist, play a different one, and later resume either
playlist at the first track that was not completed.

## Ontology Model

The media scan creates `playlist` and nested `media` entities. A `contains` link
from a playlist to each track stores its zero-based `position`.

Each playback attempt is a `playback_session` entity with these properties:

- `playlist_id`
- `track_files`: ordered file identities for this playback attempt
- `next_track_index`
- `track_count`
- `status`: `playing`, `interrupted`, `stopped`, `completed`, `failed`, or
  `superseded`
- `started_at`, `updated_at`, and optional `completed_at`

The session has a `uses` link to its playlist. The mutable cursor is stored on
the session entity. On resume, completed file identities are reconciled with the
current catalog; new songs remain unplayed even if they sort before older songs.

## Command Semantics

- `play` and `restart` create a session at index zero.
- Starting over marks older incomplete sessions for the same playlist as
  `superseded`, so they cannot unexpectedly become resume candidates later.
- `resume` reuses the newest saved session for the selected playlist, or starts
  at zero when no matching file identities remain. A fully completed selection
  is a no-op; choosing a next playlist belongs to the model.
- `stop` preserves progress and sends the HA stop action.
- `reset_playlist_ids`, used with restart, supersedes progress only for the
  concrete catalog IDs explicitly chosen by the model.
- Starting or resuming a playlist interrupts any other active session while
  retaining its cursor.
- Prompt guidance describes ordinary play/continue/restart expectations, but the
  model interprets conversational context, exceptions, and ambiguity.

The LLM identifies the playlist and operation. The backend owns cursor lookup
and never asks the LLM to calculate a track position.

All playback intent remains LLM-managed. The system prompt explicitly tells the
model to interpret likely speech-to-text variants such as "CC Psycho 3" and
"CC Cycle three" as CC Cycle 3 in weekly playlist requests.

## Playback Context

The system prompt includes the latest state for up to 30 recently updated
playlists, including completed sessions, with completed/remaining counts, next
unplayed title, status, and last activity. These are facts, not next-week actions. This lets
the LLM resolve requests such as "continue what we were listening to" without
including the complete playback history.

## Home Assistant Progress

The playlist service receives a session ID and the original starting index.
After each track reaches a real completed state, the integration posts a
`track_completed` event to the agent server. Cancellation, timeout, or an
explicit stop posts `interrupted`, `failed`, or `stopped` respectively.

Only confirmed completion advances `next_track_index`. The final track is also
observed before the session is marked complete.

If Home Assistant rejects a media path or raises another playback exception, the
integration reports `failed` for the session and retains the current cursor.
