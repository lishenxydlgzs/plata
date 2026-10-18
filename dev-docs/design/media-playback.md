# Media Playback

## Goal

Allow Plata to play and stop local audio on the Home Assistant Voice PE.

## Target Device

- Media player: `media_player.home_assistant_voice_09fe19_media_player`
- Assist satellite: `assist_satellite.home_assistant_voice_09fe19_assist_satellite`

## Media Location

Playable files live in Home Assistant's local media source:

```text
/home/lishenxydlgzs/homeassistant/media/kids_robot/
```

If Home Assistant does not already expose `/config/media` as the `local` media source, add this to `/home/lishenxydlgzs/homeassistant/configuration.yaml`:

```yaml
homeassistant:
  media_dirs:
    local: /config/media
```

Audio files remain in Home Assistant's local media source. The ontology database
owns their catalog records, playlist membership, classification, availability and
playback history. See [Ontology-owned media catalog](ontology-media-catalog.md).

## Media Catalog

At startup, filesystem reconciliation discovers audio and refreshes availability.
A one-time transactional migration imports legacy CC manifests and reuses existing
song/playlist entities. Later scans do not overwrite database metadata. Import jobs
publish new audio and classification state directly into the ontology.

The conversation agent calls `search_media` to find individual songs and playlists
using literal text or structured filters, and can inspect ordered playlist members.
Both result kinds use ontology entity UUIDs. Imported and unclassified songs are
individually discoverable. The prompt includes search instructions and recent
playback progress, rather than a list of filename/playlist IDs.

Supported extensions: `.mp3`, `.mp4`, `.wav`, `.ogg`, `.flac`, `.m4a`.
Execution accepts only catalog records whose files remain available within the
media directory; hidden paths, traversal and escaping symlinks are rejected.

## Backend Behavior

All playback requests, including stopping and negations, go to the LLM with the media catalog and saved progress. The model returns concrete `media_ids`, a `media_operation`, optional explicit `reset_playlist_ids`, and its spoken reply. The backend validates and executes these selections without choosing a different playlist or rewriting the reply.

The backend resolves selected UUIDs to songs or ordered playlist members in the
same ontology repository used by search. Historical IDs remain resolution aliases
for compatibility. A playlist with missing members cannot silently skip them.

- Single track: returns a `media_player.play_media` action
- Multiple tracks: returns a `kids_robot.play_playlist` action with a `tracks` list
- Stop music/audio/story: returns `kids_robot.stop_playback`, which cancels the
  playlist, preserves its cursor, and stops the media player

The LLM picks 3-8 tracks for playlist requests ("play some bedtime music", "play a few songs").

## Home Assistant Behavior

The HA integration executes allowlisted service actions returned by the backend:

- `media_player.play_media`, `media_player.media_stop`, `media_player.media_pause`, `media_player.media_play`
- `kids_robot.play_playlist` (custom service registered by the integration)

If an action does not specify a target, the integration uses the configured media player entity, defaulting to:

```text
media_player.home_assistant_voice_09fe19_media_player
```

## Playlist Playback

### Approach

The Voice PE does not support `MEDIA_ENQUEUE` (supported_features bitmask: 1200653). Instead, the integration implements sequential playback: play track 1, wait for it to finish, play track 2, etc.

The `kids_robot.play_playlist` service is registered by the integration on setup. It accepts:

```json
{"tracks": ["media-source://...track1.mp4", "media-source://...track2.mp4"]}
```

### Voice PE State Machine

The Voice PE media player has a non-obvious state transition pattern when `play_media` is called:

```
play_media called
  → playing (brief, <1s)
  → idle (loading/buffering phase, 5-10s)
  → playing (actual audio output begins)
  → idle (track finished)
```

The brief `playing → idle` at the start is NOT the track finishing — it's a loading artifact. A naive "wait for idle" would immediately trigger the next track before the first one has actually started playing.

### Detection Strategy

The integration uses a 3-second debounce: an `idle` state is only treated as "track finished" if the player has been in `playing` state for more than 3 seconds prior. This reliably distinguishes:

- Loading phase: `playing` for <1s → `idle` (ignored)
- Actual completion: `playing` for 30s-5min → `idle` (triggers next track)

A 1-second delay is added between tracks to avoid race conditions with the Voice PE's state reporting.

### Timeout

Each track has a 10-minute timeout. If the player doesn't transition to idle within that window (e.g., device disconnects), the playlist stops and logs a warning.

### Stop Behavior

The `kids_robot.stop_playback` service cancels the active playlist task before
calling `media_player.media_stop`. It reports the session as stopped without
advancing the cursor, so a later resume repeats the interrupted track rather
than skipping material that may not have finished.
