# Testing Plata through Home Assistant

Home Assistant provides remote interfaces for testing text, recorded audio, and
satellite playback. A person at the Voice PE is needed only to verify the physical
microphone, on-device wake word detection, and audible speaker quality. STT and
TTS can be tested remotely.

## Choose the test boundary

| Test | Covers | Does not establish |
|---|---|---|
| Agent-server `POST /conversation` on port 8200 | Plata generation and returned action contract | HA integration, action execution, STT, TTS, satellite |
| HA `POST /api/conversation/process` on port 8123, selecting Plata | HA conversation agent → Plata → HA; integration action handling | STT, TTS, microphone or speaker |
| HA `assist_pipeline/run`, `intent` → `tts` | Selected pipeline, Plata integration, synthesized reply audio | STT or automatic Voice PE playback |
| HA `assist_pipeline/run`, `stt` → `tts`, with recorded audio | STT → Plata integration → TTS; stage events and output audio | Physical microphone, on-device wake word, automatic satellite playback |
| Supported satellite `announce` action | HA-to-satellite announcement/playback path | Plata reasoning or microphone input; audible quality without a listener |
| Physical Voice PE utterance | Device wake word, microphone, configured pipeline and audible reply | Repeatable automated regression coverage by itself |

These are different entry points. Injecting audio into HA simulates a client of
its pipeline, not audio arriving at the Voice PE microphone. Supplying a
`device_id` provides device context; it does not itself route TTS audio to that
device's speaker.

## Preparation

- Use the installed HA version's available services and pipeline configuration.
  Identify the actual Plata conversation entity/agent ID, assigned pipeline ID,
  device ID, and satellite entity; do not assume entity names.
- Use an existing authorized HA access token for REST/WebSocket authentication.
  Keep it outside Git and logs. Do not paste it into committed examples.
- Use neutral synthetic input and a fresh conversation. Omit `conversation_id`
  initially so HA creates one; reuse the returned ID for follow-up checks.
- Live HA requests can persist conversation/memory records and execute device
  actions. Start with “Deployment check: say ready.” Use timer, media, or satellite
  actions only when actual device effects are intended. Disposable backend tests
  do not prove that the running HA integration executes actions correctly.
- Respect the Gemini free-tier 15 RPM limit. Tool rounds and fallback attempts
  can each consume calls. Use health endpoints for repeated connectivity checks.

## 1. Test text through the HA conversation API

Send an authenticated JSON POST to `http://<ha-host>:8123/api/conversation/process`:

```json
{
  "text": "Deployment check: say ready.",
  "language": "en",
  "agent_id": "conversation.REPLACE_WITH_PLATA_ENTITY"
}
```

Authentication uses `Authorization: Bearer <token>` and
`Content-Type: application/json`. Select Plata explicitly: the default agent may
be HA's built-in assistant. The equivalent WebSocket command has
`"type": "conversation/process"` and a unique numeric `id`.

Check the conversation response's `response_type` for errors and inspect its
speech text, returned `conversation_id`, and `continue_conversation`. HTTP 200
alone is not a successful conversation. Confirm that the request reached Plata.
A returned timer action in a direct backend test is not evidence that HA executed
it; test the HA entry point and resulting device/service state separately.

Reference: [HA Conversation API](https://developers.home-assistant.io/docs/intent_conversation_api/).

## 2. Test the selected pipeline with text or recorded audio

Connect to HA's authenticated `/api/websocket`, complete its authentication
handshake, and use `assist_pipeline/pipeline/list` to discover the pipeline used
by the Voice PE. First test text through intent recognition and TTS:

```json
{
  "id": 10,
  "type": "assist_pipeline/run",
  "pipeline": "REPLACE_WITH_PIPELINE_ID",
  "start_stage": "intent",
  "end_stage": "tts",
  "input": {"text": "Deployment check: say ready."}
}
```

For STT coverage, start at `stt` instead:

```json
{
  "id": 11,
  "type": "assist_pipeline/run",
  "pipeline": "REPLACE_WITH_PIPELINE_ID",
  "start_stage": "stt",
  "end_stage": "tts",
  "input": {"sample_rate": 16000}
}
```

1. Prepare a short synthetic spoken recording in the raw audio format expected
   by the installed pipeline/STT provider. Match its sample rate and metadata;
   do not send an entire WAV container as raw audio samples.
2. Read `runner_data.stt_binary_handler_id` from `run-start` and wait for
   `stt-start` before sending audio.
3. Send binary audio chunks, each prefixed with the one-byte handler ID.
4. End the audio stream with a binary message containing only that handler byte.
5. Verify the `stt-end` transcript, `intent-end` conversation response, and
   `tts-end` output. Fail on an `error` event; `run-end` alone is insufficient.
6. Retrieve the returned TTS audio URL and verify usable audio is generated.
   Playback on the Voice PE requires a separate supported playback/announcement
   action. A successful TTS result does not prove speaker playback.

HA can also start a pipeline at `wake_word` when a server-side wake engine is
configured. That does not test the Voice PE's on-device wake-word engine.

References: [Assist pipeline API](https://developers.home-assistant.io/docs/voice/pipelines/)
and [WebSocket authentication](https://developers.home-assistant.io/docs/api/websocket/).

## 3. Test supported satellite actions

Inspect the installed satellite's capabilities before using these actions:

- `assist_satellite.announce`: play a synthetic message or supported media on the
  selected satellite. Check the action result and satellite state transitions;
  a listener is still needed to confirm audible quality.
- `assist_satellite.start_conversation`: initiate an interaction on a supported
  satellite. It can prompt and listen, but does not inject a synthetic microphone
  response. Agent/firmware support must be checked separately.

These may produce sound in the household. Do not change volume, wake-word
settings, pipeline assignments, or firmware merely to perform a smoke test.

Reference: [Assist Satellite actions](https://www.home-assistant.io/integrations/assist_satellite/).

## Rollout evidence to record

Record which boundary was exercised, HA/firmware versions, selected agent and
pipeline (use placeholders in committed examples), latency, stage success/error,
and whether a real device action or audible playback was checked. Keep tokens,
private transcripts, household records and recordings out of committed artifacts.

The 2026-10-03 migration verified direct backend live Gemini, tool and timer
response tests plus browser AG-UI chat. It did **not** exercise these HA
conversation/pipeline/satellite interfaces. This guide documents future tests;
it does not upgrade that rollout's coverage retrospectively.
