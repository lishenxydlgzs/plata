# Telegram audio uploads can duplicate after ambiguous acceptance

Status: Known limitation

Telegram's Bot API does not provide an idempotency key for `sendAudio` or
`sendMediaGroup`. If Telegram accepts an upload and the connection fails before
the response arrives, or Plata stops before committing completion, retrying the
pending batch can deliver it twice. Exactly-once delivery cannot be guaranteed.

Plata commits completion after each batch and never retries earlier completed
batches. Identical tool calls for the same incoming update and selection are
deduplicated durably. Users may still see a duplicate of the current batch after
an ambiguous failure. Avoiding every duplicate would require accepting possible
lost delivery instead; the current behavior prioritizes eventual delivery.

Covered by transport retry/progress tests. No live user data is needed to reproduce.
