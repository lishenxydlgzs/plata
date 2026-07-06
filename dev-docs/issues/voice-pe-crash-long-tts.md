# Voice PE crashes on long TTS responses

## Status: Open (workaround in place)

## Problem

The Home Assistant Voice PE (ESP32-S3, firmware 26.6.0) crashes with an `IllegalInstruction` fault during TTS playback of longer responses (~40+ words). The device stutters/repeats the last word, then reboots.

## Crash log

```
*** CRASH DETECTED ON PREVIOUS BOOT ***
Reason: Fault - IllegalInstruction
Crashed core: 0
PC: 0x403858C4
Backtrace: 0x403858C1 0x40385889 0x40389CFA 0x420955DF 0x42079E81 0x4201B2C1 0x4201A56E 0x4201AD2E
```

## Trigger

Response that caused the crash (46 words, 238 chars):
> "I'm still learning how to remember everything we say, so I don't quite have a record of our conversation from yesterday. Why don't you tell me what we talked about so we can pick up right where we left off?"

## Root cause

Known firmware bug in Voice PE. The ESP32's audio pipeline (FLAC decode + DMA playback) likely overflows a buffer or exhausts task stack memory when processing longer TTS audio. Multiple reports exist:

- https://github.com/esphome/home-assistant-voice-pe/issues/355
- https://github.com/esphome/home-assistant-voice-pe/issues/271
- https://github.com/esphome/home-assistant-voice-pe/issues/382
- https://community.home-assistant.io/t/voice-pe-seems-to-crash/931324

## Current workaround

- `max_output_tokens` set to 80 (was 150)
- System prompt instructs 1-3 short sentences
- Safe zone: ~30-40 words / 2 short sentences

## Proper fix

Wait for upstream firmware fix from ESPHome/Nabu Casa. Monitor:
- https://github.com/esphome/home-assistant-voice-pe/releases
- Check if future firmware (>26.6.0) resolves the audio playback crash

## When to revisit

- When a new Voice PE firmware is released, test with longer responses
- If fixed, increase `max_output_tokens` back to 150 for richer conversations
