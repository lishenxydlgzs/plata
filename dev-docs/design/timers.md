# Timers

## Goal

Let a child say “set a timer for five minutes” and hear a short alert when it
expires.

## Design

The LLM recognizes timer intent and returns a structured `timer_seconds`
field. The agent server validates it and returns a `kids_robot.start_timer`
action. This keeps timer requests resilient to natural language and imperfect
speech transcription. The Home Assistant integration owns the countdown via
`async_call_later`; on expiry it calls `media_player.play_media` on the
configured Voice PE.

The alert URI is `media-source://media_source/local/kids_robot/timer.wav`.
A bundled short chime is copied to the Kids Robot media directory by
`./scripts/deploy.sh --ha`.
Timers are intentionally in-memory for this initial implementation: restarting
Home Assistant cancels timers that have not yet expired.

## Constraints

- Accepted durations are one second through 24 hours.
- The LLM accepts natural timer wording and returns whole-second durations.
- The integration allowlists only the new `kids_robot.start_timer` service.
