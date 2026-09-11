# Family Values and Kid Event Logging

> Superseded for new conversations by [Household guidance and learning memory](household-guidance-learning.md).
> Existing family-value and kid-event records and APIs remain available for compatibility.

## Goal

Allow a parent to define family values in English, then describe a child behavior
moment so Plata can respond with value-aware encouragement or correction and save
the moment for later reward or redemption review.

## Data Model

- `family_value` entity: stable parent-authored value with `key`, `description`,
  `guidance`, and `enabled`.
- `kid_event` entity: parent-described child moment with `child_name`, `summary`,
  `event_type`, `matched_value_keys`, `parent_note`, `confidence`,
  `conversation_id`, `source_text`, and `timestamp`.
- `message --reports--> kid_event`
- `kid_event --reflects--> family_value`

## Chat Behavior

The chat system prompt includes enabled family values and asks the LLM to:

- use the values for warm encouragement or gentle correction when a parent
  reports child behavior
- avoid shame and focus on behavior plus a practical next step
- return structured `kid_events` only when a child behavior moment is clearly
  described

Ordinary questions, music requests, and casual chat should not create kid event
records.

## API

- `GET /api/family-values`
- `POST /api/family-values`
- `GET /api/kid-events?child_name=...&limit=...`

The existing graph review UI can visualize the new entity and link types.
