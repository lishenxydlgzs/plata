# Voice PE crashes during TTS playback

## Status: Open (workaround in place, may be hardware defect)

## Problem

The Home Assistant Voice PE (ESP32-S3, firmware 26.6.0) crashes during or before TTS playback. Multiple distinct crash signatures observed on the same device, suggesting possible hardware instability rather than a single software bug.

## Crash #1 — IllegalInstruction (2026-07-05 ~14:00)

```
*** CRASH DETECTED ON PREVIOUS BOOT ***
Reason: Fault - IllegalInstruction
Crashed core: 0
PC: 0x403858C4 (IRAM — audio/DMA code region)
Backtrace: 0x403858C1 0x40385889 0x40389CFA 0x420955DF 0x42079E81 0x4201B2C1 0x4201A56E 0x4201AD2E
```

**Trigger:** TTS playback of a 46-word response:
> "I'm still learning how to remember everything we say, so I don't quite have a record of our conversation from yesterday. Why don't you tell me what we talked about so we can pick up right where we left off?"

## Crash #2 — InstructionFetchError (2026-07-05 ~21:19)

```
*** CRASH DETECTED ON PREVIOUS BOOT ***
Reason: Fault - InstructionFetchError
Crashed core: 1
PC: 0x3C1ABC70 (flash/PSRAM data cache region — NOT where code should live)
Backtrace: 0x42159D42
```

**Trigger:** TTS playback of a 29-word response (within "safe" range):
> "It looks like it will be a beautiful, sunny day with a high of 75 degrees! That sounds like perfect weather for us to play outside together."

## Crash #3 — Immediate crash on button press (2026-07-05 evening)

No crash log captured yet (device hadn't reconnected). Voice PE crashed immediately on button press — sharp repeating sound, then reboot. No request reached the agent server.

## Analysis

| | Crash #1 | Crash #2 |
|--|--|--|
| Reason | IllegalInstruction | InstructionFetchError |
| Core | 0 (app core) | 1 (protocol/radio core) |
| PC address | 0x4038xxxx (IRAM) | 0x3C1Axxxx (flash/PSRAM) |
| Backtrace | 8 frames | 1 frame |
| Response length | 46 words | 29 words |

Two completely different crash signatures (different cores, different fault types, different memory regions) on the same device points toward:

1. **Hardware defect** — failing flash chip or PSRAM causing intermittent read errors
2. **Firmware corruption** — flash contents damaged from a previous hard crash
3. **Power instability** — despite trying different 5V/2A plugs, audio playback + WiFi TX may momentarily exceed supply capacity

## Upstream references

- https://github.com/esphome/home-assistant-voice-pe/issues/355
- https://github.com/esphome/home-assistant-voice-pe/issues/271
- https://github.com/esphome/home-assistant-voice-pe/issues/382
- https://community.home-assistant.io/t/voice-pe-seems-to-crash/931324

## Current workaround

- `max_output_tokens` set to 80 (was 150)
- System prompt instructs 1-3 short sentences
- Note: crash #2 and #3 show this doesn't fully prevent crashes

## Next steps

1. Power cycle (unplug 30+ seconds) to fully reset hardware state
2. Reflash firmware (force reinstall 26.6.0) to rule out flash corruption
3. If crashes persist after reflash, likely hardware defect — consider replacement
4. Monitor upstream for firmware fixes

## When to revisit

- After reflash, test stability over several days
- When new Voice PE firmware is released (>26.6.0)
- If device is stable, increase `max_output_tokens` back to 150
