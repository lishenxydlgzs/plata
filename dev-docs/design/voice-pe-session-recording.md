# Voice PE session recording: protocol feasibility

Status: source inspection complete; recording prototype and endurance tests pending.
Inspected 2026-09-11. No firmware, deployed configuration, or recording state was changed.

## Installed versions

Read-only inspection confirmed Home Assistant 2026.6.4, aioesphomeapi 45.3.1,
and Voice PE 26.6.0 built with ESPHome 2026.6.0. No audio or conversation data
was accessed. Conclusions below use matching released firmware and HA source.

## Conclusion

Prototype a custom Home Assistant speech-to-text provider that records its input
stream, using the stock PE firmware and HA's existing ESPHome connection. This
appears feasible from source; it is not yet a tested recording feature. An
automation can orchestrate this provider, but stock automation actions alone do
not implement it. Custom PE firmware is not the first approach to pursue.

## Findings

- ESPHome already sends microphone PCM through the native API. In
  `VoiceAssistant::start_streaming`, the device must first be waiting for a
  pipeline response; sending a response to an idle device cannot start capture.
- `STREAMING_MICROPHONE` keeps draining microphone buffers. No fixed maximum
  recording duration or local silence endpoint was found in that state. The
  conversation-ID timeout resets an ID, rather than ending capture.
- `client_subscription` permits only one voice-assistant client. A second
  standalone recorder cannot subscribe alongside HA. Keep HA as the owner.
- HA's STT provider interface exposes `SpeechAudioProcessing` with
  `requires_external_vad=False`. The pipeline then skips its speech-ending VAD
  segmenter. A recorder provider can consume speech and silence until explicitly
  stopped or cancelled, without changing PE firmware or HA core.
- HA's satellite path calls `async_pipeline_from_audio_stream` directly. No
  overall 300-second timeout was found in this inspected path. The WebSocket
  API's documented default timeout is not evidence of a five-minute limit here.
- Stock PE single-click handling invokes `voice_assistant.stop` when Assist is
  running, unless an earlier-priority condition such as a ringing timer applies.
  HA handles the device stop request as cancellation. The recorder must finalize
  its partial file on cancellation and then propagate cancellation normally.
- The stock announcement protocol includes `start_conversation`; after playback,
  the firmware starts another microphone/pipeline run. This supplies a candidate
  confirmation-then-record mechanism with the recording pipeline selected first.
- Stock wake-word handling can stop an already-running assistant. Accidental
  wake-word interruption must be tested. This does not establish recognition of
  the full phrase "stop recording" during a recording.
- Native audio messages have no recording sequence number. The released
  `stream_api_audio_` consumes chunks after calling `send_message` without checking
  its return value. TCP alone therefore does not prove lossless capture; test
  sample duration, known reference tones, network stalls, and device stability.

## Proposed minimum prototype

1. Add a custom STT entity named Session Recorder, with external VAD disabled.
   Save 16 kHz, 16-bit mono input incrementally using a bounded writer queue;
   never block HA's event loop or accumulate a session in RAM. Report writer
   overload instead of silently dropping samples.
2. Create a separate Assist pipeline selecting this provider. Initially select
   it manually, start using the PE button, and stop using the button. Avoid
   relying on an LLM or transcription service during the endurance experiment.
3. Save and close the WAV file on normal stop, cancellation, disconnect, and HA
   unload. Differentiate successful stop from interrupted capture where possible.
   Keep all audio private and outside Git. Transcribe only after finalization.
4. Add session status and stop controls. Handle completion through a dedicated
   deterministic path so control text or full recordings never reach the normal
   conversation agent by accident. Restore the prior pipeline during cleanup.
5. Once manual recording works, add an HA automation that remembers the prior
   pipeline, selects Session Recorder, and invokes satellite start-conversation
   with a brief confirmation. Validate completion ordering and pipeline restoration
   before connecting a spoken start command to this automation.

## Validation still required

- Five-minute recording, then 60-minute and two-hour runs with long silences and
  known periodic tones; compare sample counts, wall time, and audible continuity.
- Repeated start/stop, button cancellation, timer interference, wake-word
  interruption, hardware mute, Wi-Fi disconnect, HA restart, and disk-write failure.
- Verify normal Assist works after restoration and failed recording startup.
- No live audio test was performed during this inspection. Source compatibility
  is encouraging but cannot establish sustained recording reliability.

## Sources

- [ESPHome 2026.6.0 voice-assistant state machine](https://github.com/esphome/esphome/blob/2026.6.0/esphome/components/voice_assistant/voice_assistant.cpp)
- [Voice PE 26.6.0 configuration](https://github.com/esphome/home-assistant-voice-pe/blob/26.6.0/home-assistant-voice.yaml)
- [HA 2026.6.4 ESPHome satellite receiver](https://github.com/home-assistant/core/blob/2026.6.4/homeassistant/components/esphome/assist_satellite.py)
- [HA STT processing settings](https://github.com/home-assistant/core/blob/2026.6.4/homeassistant/components/stt/models.py)
- [HA STT entity interface](https://github.com/home-assistant/core/blob/2026.6.4/homeassistant/components/stt/__init__.py)
- [HA pipeline VAD and stream handling](https://github.com/home-assistant/core/blob/2026.6.4/homeassistant/components/assist_pipeline/pipeline.py)
- [HA satellite pipeline entry](https://github.com/home-assistant/core/blob/2026.6.4/homeassistant/components/assist_satellite/entity.py)
- [HA audio pipeline entry](https://github.com/home-assistant/core/blob/2026.6.4/homeassistant/components/assist_pipeline/__init__.py)
