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

The agent server handles stop commands deterministically. For possible play requests, it sends the user request and media catalog metadata to the LLM selector. The selector returns either a catalog `media_id` or `null`.

Supported first-pass catalog items:

- Bedtime Music: emits `media_player.play_media` for `bedtime.mp3`
- Bedtime Story: emits `media_player.play_media` for `story.mp3`
- Bingo: emits `media_player.play_media` for `BINGO.mp4`
- Stop music/audio/story: emits `media_player.media_stop`

The backend returns these as `actions` in the existing structured response format.

## Home Assistant Behavior

The HA integration executes only allowlisted `media_player` service actions returned by the backend. If an action does not specify a target, the integration uses the configured media player entity, defaulting to:

```text
media_player.home_assistant_voice_09fe19_media_player
```
