# Full Custom Conversation Agent Requirements for the Kid Robot Project

## Overview

This document defines the requirements for the **full custom** architecture in which the robot’s backend server becomes the effective conversational brain behind Home Assistant Assist. In Home Assistant’s architecture, Assist does not call an arbitrary HTTP server directly; instead, it routes text to a conversation agent registered inside Home Assistant, and that conversation agent can then call an external backend server for processing.[cite:264][cite:270] The goal of this project is to build a family-oriented homeschool companion robot that is always available, voice-capable, programmable, safe for children, and adaptable to routines, teaching, and play.[cite:183][cite:187]

## Goal

The target system should allow a child or parent to speak to the robot through Assist, have the request routed through a custom Home Assistant conversation agent, processed by a local backend server, and then returned as a short, child-friendly spoken response.[cite:264][cite:76] The system should support homeschool interactions, role-play, routine assistance, and future hardware control such as buttons and LEDs while remaining open and programmable.[cite:183][cite:187][cite:190]

## Architecture summary

The intended full-custom flow is:

1. User speaks through Assist or another Home Assistant voice client.[cite:76]
2. Speech-to-text converts audio to text, for example using Whisper.[cite:186]
3. Home Assistant sends the text to a **custom conversation agent** registered inside Home Assistant.[cite:264][cite:270]
4. The custom conversation agent forwards the request to a local backend server over HTTP or another internal protocol.[cite:264][cite:273]
5. The backend server decides how to respond, optionally calling an LLM, local scripts, game logic, or Home Assistant APIs.[cite:72][cite:261]
6. The custom conversation agent converts the backend response into a Home Assistant conversation result and returns it to Assist.[cite:264]
7. Home Assistant speaks the response through Piper or another TTS engine.[cite:186][cite:117]

## Core design principle

The backend server should **not** be treated as a raw chatbot endpoint only. It should be treated as a robot-control brain with structured routing between conversational chat, homeschool guidance, pretend play, routines, and smart-home actions.[cite:183][cite:187] This matters because the robot is meant to be more than a generic assistant; it is intended to become a dependable family artifact that supports learning and play in daily life.[cite:183][cite:189]

## Scope

### In scope

- A custom Home Assistant integration that registers a conversation agent.[cite:264]
- A local backend server that receives text requests from that agent and returns structured responses.[cite:273][cite:261]
- Support for short conversational replies, child-safe behavior, and clear spoken output.[cite:76]
- Support for homeschool tasks such as quiz mode, recitation, explanations, and memorization support.[cite:183][cite:187]
- Support for imaginative play, role-play, and parent-defined scripted modes.[cite:183][cite:187]
- Optional support for hardware-triggered workflows such as button-initiated interaction and LED status updates.[cite:190][cite:231]

### Out of scope for version 1

- Full robotics motion control.
- Camera vision pipelines.
- Complex multi-user identity tracking.
- Open internet access for arbitrary browsing by children.
- Unrestricted smart-home control across the entire house.

## Functional requirements

### FR1: Home Assistant conversation agent integration

The system must include a custom Home Assistant integration that exposes a valid conversation agent to Assist.[cite:264][cite:270] The integration must be selectable as the **Conversation agent** inside **Settings > Voice Assistants** in Home Assistant, in the same way other conversation agents such as Ollama are selected.[cite:76][cite:86]

### FR2: Message forwarding to local server

The conversation agent must forward the user’s recognized text to a local backend server endpoint for processing.[cite:264][cite:273] The message payload should include, at minimum, the incoming text, a conversation identifier, timestamp, language, and optional source metadata such as device or satellite ID when available.[cite:262][cite:264]

### FR3: Structured backend response

The backend server must return a structured response that the custom conversation agent can convert into a Home Assistant conversation result.[cite:264] The response format should support at least:

- `reply_text`: the text to be spoken or shown,
- `mode`: the conversation mode such as chat, teacher, story, or routine,
- `continue_conversation`: whether the agent should expect a follow-up,
- `actions`: optional machine-readable actions for Home Assistant, robot hardware, or logging.

### FR4: Multi-turn conversation support

The system must support continuing a conversation across multiple turns using a stable conversation ID.[cite:262][cite:265] This is necessary for quiz mode, storytelling, clarification, and any mode in which the robot should preserve short-term context.[cite:265][cite:267]

### FR5: Child-safe conversational behavior

The backend must produce short, spoken, child-friendly replies suitable for a homeschool companion and playmate robot.[cite:76][cite:218] The system should avoid long, formal, email-like, or adult-oriented responses and instead favor concise, warm, easy-to-understand speech.[cite:76][cite:218]

### FR6: Mode-aware routing

The backend server must support at least the following conversation modes:

- **Chat mode**: general friendly responses.[cite:76]
- **Teacher mode**: step-by-step teaching, quiz prompts, recitation, memorization help.[cite:183][cite:187]
- **Play mode**: age-appropriate role-play, characters, storytelling, creative prompts.[cite:183][cite:187]
- **Parent helper mode**: routines, reminders, summaries, and administrative interactions.[cite:183]

### FR7: Optional LLM use

The backend server may use an LLM such as Ollama, but the overall architecture must not depend on the LLM being the only source of behavior.[cite:86][cite:72] Some tasks should be handled with deterministic local logic or scripts, especially when reliability matters, such as starting a quiz mode or acknowledging a hardware button press.[cite:94][cite:183]

### FR8: Home Assistant action bridge

The backend server must be able to trigger Home Assistant actions when needed, using Home Assistant’s REST API or other supported mechanisms.[cite:261] This should support limited actions such as triggering scripts, setting helpers, toggling LEDs through companion logic, or initiating parent-approved routines.[cite:261][cite:190]

### FR9: Hardware event compatibility

The architecture should allow hardware controls such as a large GPIO-connected button and status LEDs to initiate or reflect conversation state.[cite:190][cite:231] This includes button-triggered prompts such as “start listening,” “start quiz mode,” or “repeat last answer,” and LED states such as idle, listening, thinking, or speaking.[cite:190][cite:241]

### FR10: Logging and observability

The system must log incoming user text, chosen route or mode, backend processing outcome, returned reply text, and error conditions for debugging.[cite:204][cite:205] Logs should be readable enough to troubleshoot issues such as tool failures, malformed payloads, slow LLM responses, or prompt problems.[cite:203][cite:205]

## Non-functional requirements

### NFR1: Local-first operation

The system should run fully on the local network by default and should not require cloud services for core operation.[cite:86][cite:249] This aligns with the project’s privacy-first and open-platform goals.[cite:183][cite:189]

### NFR2: Low complexity for daily family use

The user-facing experience must remain simple: press a button or speak, wait for a response, hear a short answer.[cite:183][cite:190] Internal architectural complexity is acceptable only if it does not leak into daily family operation.[cite:183]

### NFR3: Fail-safe behavior

If the backend server, LLM, or action layer fails, the system must fail gracefully with a short apology or fallback response rather than hanging or exposing stack traces.[cite:203][cite:205] Failure cases must be safe for children and should not leave hardware or automations in an ambiguous state.[cite:205]

### NFR4: Response brevity

Default spoken responses should usually be one to three short sentences unless a parent or script explicitly requests a longer explanation.[cite:76][cite:218] This is necessary because the robot’s primary interface is spoken conversation rather than a text screen.[cite:76]

### NFR5: Extensibility

The design should make it easy to add future features such as additional buttons, LEDs, sensors, curriculum modules, game packs, or better model backends without rewriting the full architecture.[cite:183][cite:187]

## Home Assistant integration requirements

### Integration packaging

The custom integration should be structured as a standard Home Assistant custom component with at least:

- `manifest.json`,
- `__init__.py`,
- `conversation.py`,
- optional `config_flow.py`,
- optional translation and diagnostics files.

Home Assistant’s conversation system expects an integration-defined conversation entity that implements the conversation handling interface.[cite:264][cite:270]

### Conversation entity behavior

The integration must implement a conversation entity derived from Home Assistant’s conversation entity framework.[cite:264] It should:

- accept incoming user text,
- preserve and pass along conversation context,
- send requests to the backend server,
- map backend responses into Home Assistant speech responses,
- handle errors cleanly.

### Configuration surface

The integration should allow configuration of at least:

- backend server base URL,
- timeout,
- default mode,
- retry policy,
- whether Home Assistant actions are enabled,
- diagnostic logging level.

## Backend server requirements

### API endpoints

The backend should expose at minimum one main endpoint such as:

- `POST /conversation`

Additional optional endpoints may include:

- `POST /mode/teacher/start`
- `POST /mode/story/start`
- `POST /hardware/button`
- `POST /health`
- `GET /status`

### Input schema

The main conversation endpoint should accept JSON containing at least:

```json
{
  "text": "How are you today?",
  "conversation_id": "string",
  "language": "en",
  "source": "assist",
  "device_id": "optional",
  "satellite_id": "optional",
  "timestamp": "ISO-8601"
}
```

### Output schema

The backend should return JSON such as:

```json
{
  "reply_text": "I am doing well. Ready for our next adventure.",
  "mode": "chat",
  "continue_conversation": true,
  "actions": []
}
```

### Internal routing

The backend should include a router that decides whether a message goes to:

- deterministic script logic,
- homeschool lesson logic,
- role-play/story logic,
- parent command logic,
- a local LLM backend.[cite:183][cite:187][cite:72]

## Safety requirements

The system must enforce child-safe behavior across all modes.[cite:76] At minimum, it should:

- block unsafe or age-inappropriate responses,
- avoid pretending to have performed physical actions unless confirmed,
- avoid open-ended web access for the child-facing assistant,
- limit device control to explicitly exposed or approved actions.[cite:72][cite:86]

Parent-defined routines or administrative commands should be separable from child-facing free conversation.[cite:183] This separation helps prevent a playful child interaction path from accidentally becoming a privileged home-control path.[cite:86][cite:216]

## Conversation quality requirements

The system should support prompt or policy rules that keep replies:

- short,
- spoken rather than essay-like,
- kind and encouraging,
- age-appropriate,
- aligned with the robot persona.[cite:76][cite:218]

The system should specifically avoid the failure mode already observed in local LLM testing, where the model responded with long, generic, adult email-style prose to a simple question.[cite:218]

## Hardware integration requirements

The backend design should anticipate physical robot inputs and outputs even if they are not all implemented in version 1.[cite:190][cite:231] At minimum, it should be easy to map:

- button press  initiate interaction,
- long press  change mode,
- LED state  idle/listening/thinking/speaking,
- optional future parent button  admin mode.[cite:190]

GPIO-based button and LED control is a natural fit for Raspberry Pi and can be driven through Python libraries such as GPIO Zero.[cite:241][cite:240]

## Error handling requirements

The system must explicitly handle:

- backend unavailable,
- timeout waiting for backend,
- malformed backend JSON,
- LLM failure,
- Home Assistant action failure,
- conversation context mismatch.

In each case, the assistant should respond with a short fallback message and log the full technical details for the parent or developer.[cite:203][cite:205]

## Testing requirements

The build should support staged testing:

1. Test backend API directly with curl or Postman.
2. Test the custom Home Assistant integration without voice, using typed text if possible.
3. Test the conversation agent in Assist with a basic prompt.
4. Test multi-turn conversations with a stable conversation ID.[cite:262][cite:265]
5. Test homeschool mode, story mode, and hardware-triggered flows separately.[cite:183][cite:187]
6. Test fallback behavior by intentionally stopping the backend server.[cite:205]

## Milestones

| Milestone | Deliverable | Success criteria |
|---|---|---|
| M1 | Backend server skeleton | Accepts JSON text input and returns valid reply JSON. |
| M2 | Custom HA conversation integration | Appears as a selectable conversation agent in Home Assistant.[cite:264] |
| M3 | End-to-end Assist flow | Spoken input reaches backend and returns spoken response. |
| M4 | Multi-mode routing | Chat, teacher, and play modes all work reliably. |
| M5 | Hardware coupling | Button and LED states influence and reflect conversation flow. |
| M6 | Parent-safe control path | Limited approved routines or actions can be triggered safely. |

## Recommended implementation strategy

A phased approach is strongly recommended. First, build the backend as a simple local API that always echoes or returns a canned short reply.[cite:248][cite:261] Next, build the custom Home Assistant conversation integration so Assist can call the backend.[cite:264] Only after that basic path is stable should the backend gain mode routing, LLM support, Home Assistant action calls, and GPIO-connected hardware logic.[cite:183][cite:190]

This phased strategy reduces confusion between problems in Home Assistant integration, network transport, prompt design, and application logic.[cite:203][cite:205] It also keeps the project aligned with the broader goal of building a dependable, family-friendly homeschool robot rather than a fragile demo.[cite:183][cite:187]
