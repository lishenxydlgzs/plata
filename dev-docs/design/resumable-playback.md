# Resumable Playlist Playback

## Goal

Allow Plata to stop one playlist, play a different one, and later resume either
playlist at the first track that was not completed.

## Ontology Model

The media scan creates `playlist` and nested `media` entities. A `contains` link
from a playlist to each track stores its zero-based `position`.

Each playback attempt is a `playback_session` entity with these properties:

- `playlist_id`
- `next_track_index`
- `track_count`
- `status`: `playing`, `interrupted`, `stopped`, `completed`, `failed`, or
  `superseded`
- `started_at`, `updated_at`, and optional `completed_at`

The session has a `uses` link to its playlist. The mutable cursor is stored on
the session entity; media ordering remains on the playlist's `contains` links.

## Command Semantics

- `play` and `restart` create a session at index zero.
- Starting over marks older incomplete sessions for the same playlist as
  `superseded`, so they cannot unexpectedly become resume candidates later.
- `resume` reuses the newest incomplete session for the selected playlist, or
  starts at zero when no incomplete session exists.
- Starting or resuming a playlist interrupts any other active session while
  retaining its cursor.
- A plain request such as "play week 5" starts from the beginning. "Continue
  week 5" resumes saved progress.

The LLM identifies the playlist and operation. The backend owns cursor lookup
and never asks the LLM to calculate a track position.

All playback intent remains LLM-managed. The system prompt explicitly tells the
model to interpret likely speech-to-text variants such as "CC Psycho 3" and
"CC Cycle three" as CC Cycle 3 in weekly playlist requests.

## Playback Context

The system prompt includes up to three recently updated incomplete sessions,
with playlist ID, completed count, total count, and next track title. This lets
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
