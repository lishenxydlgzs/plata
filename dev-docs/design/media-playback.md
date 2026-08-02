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

The agent references those files with `media-source` URIs generated from `packages/agent-server/src/agent_server/media_catalog.json`:

```text
media-source://media_source/local/kids_robot/bedtime.mp3
media-source://media_source/local/kids_robot/story.mp3
media-source://media_source/local/kids_robot/BINGO.mp4
```

The HA deploy path creates this directory automatically with `./scripts/deploy.sh --ha`. Place audio files there before using the commands.

## Media Catalog

The catalog is built dynamically by scanning the media directory at runtime. No static JSON file is needed. Each audio file found produces a catalog entry:

- `id`: derived from the filename stem (lowercased, hyphens/spaces replaced with underscores)
- `title`: derived from the filename stem (underscores/hyphens become spaces, title-cased)
- `file`: the actual filename on disk
- `media_content_type`: always `music`

Supported extensions: `.mp3`, `.mp4`, `.wav`, `.ogg`, `.flac`, `.m4a`

To add a playable file, just copy the audio into the HA media directory with a descriptive filename (e.g., `bedtime_music.mp3`, `bingo_song.mp3`). The filename becomes the title the LLM uses for matching, so name files clearly.

## Backend Behavior

The agent server handles stop commands deterministically. For play requests, it sends the user text and media catalog to the LLM, which returns `media_ids` (a list of catalog IDs to play).

- Single track: returns a `media_player.play_media` action
- Multiple tracks: returns a `kids_robot.play_playlist` action with a `tracks` list
- Stop music/audio/story: returns `media_player.media_stop`

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

Stopping the media player mid-playlist causes the state to go to `idle`. Since the playlist handler is waiting for `playing > 3s → idle`, a stop during the loading phase (before 3s of playing) will cause the handler to timeout and abort. A stop during actual playback will be detected as completion and the next track will play. To fully stop a playlist, the user should say "stop the music" which sends `media_player.media_stop` — but the playlist handler will interpret the resulting idle as completion. 

Future improvement: cancel the playlist task when a stop command is received.
