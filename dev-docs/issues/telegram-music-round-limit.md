# Telegram music delivered followed by generic failure reply

Status: Resolved — fixed model-round caps removed; covered by regression tests

A conversation could queue music successfully and then exceed the four-model-round
limit before generating its closing reply. Durable audio delivery continued, while
the chat handler returned its generic generation-failure message.

Removed fixed model-round caps consistently across structured conversations,
streaming agents and the legacy logbook loop. The agent now chooses when to end;
existing per-call timeouts, browser deadlines, cancellation and tool authorization
remain. Tests execute five Telegram tool rounds including music delivery, then a
sixth model call for the successful closing reply. No live user data is required.

Other model failures can still occur after a durable tool effect. This change
addresses the round-limit cause; it does not guarantee successful final generation
on network failures, timeouts or malformed model output.
