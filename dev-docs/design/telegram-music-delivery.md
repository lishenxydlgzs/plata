# Telegram music delivery

## Intent and agent contract

Expose `send_music(media_ids)` only during authenticated Telegram turns. The same
chat agent uses catalog search to select songs or playlists; instructions guide
selection and clarification, without keyword routing or playback substitution.
The tool returns durable queue acceptance, track counts and skipped-file reasons,
not a claim that Telegram has already delivered the audio. Robot tools and playback
state remain unchanged.

## Authorization

The Telegram transport binds a request-local context after validating the private
chat and paired sender. Context contains the service, account grant, conversation
and update ID; it is never part of the public conversation request or model tool
arguments. Tool registration requires matching Telegram source, person and
conversation. The executor independently rechecks context and the live account.
The destination is always the paired chat. Each pairing has a random grant ID;
queued audio is checked against that grant before every upload. Revocation deletes
pending jobs, and reconnecting cannot resurrect them. `/new` keeps the account grant.

## Catalog and files

Resolve exact catalog UUIDs, expand playlist membership in saved order and snapshot
song IDs and titles. Deduplicate overlapping selections while preserving first
occurrence. Bound selections to 20 IDs and expanded output to 200 tracks per call;
return an error rather than silently truncate. Skip hidden, missing, empty,
unsupported or oversized files with explicit reasons; do not convert or download.
Only MP3/M4A up to 50 MB are supported. Revalidate catalog paths and files at delivery
so deleted files and paths escaping the media directory cannot be uploaded.

## Durable delivery

Extend the existing SQLite outbox with typed audio payloads and grant IDs, using
additive migrations for existing installations. Store one job per ordered batch of
up to ten tracks; one-track batches use `sendAudio`, others `sendMediaGroup`.
Use multipart uploads without exposing media on a public HTTP endpoint. Put titles
in audio metadata and a bounded numbered track list in the batch caption.
Persist all batches atomically before returning `queued`; a unique request/selection
key prevents identical tool calls in the same update from queueing twice.

Delete each successful batch immediately and commit, preserving progress across
restarts. Retry transient network/API failures through the existing rate-limit
backoff; never replay earlier successful batches. Permanent failures and files
invalidated after queueing produce a text notice and account delivery error, while
later batches can continue. Telegram has no upload idempotency key: a timeout or
crash after remote acceptance but before the local commit can duplicate the current
batch. Record this limitation in the issue tracker. Bot switching clears queued
work. Local state contains IDs and titles, not audio bytes or credentials in logs.

## Verification

Use fake model/tool execution and fake Telegram APIs: channel filtering, forged
public source rejection, context mismatch and revocation, ordered playlist
expansion, bounds and unsafe/unsupported files, batching, multipart requests,
transient/permanent failures, restart progress, migration and request deduplication.
Run the backend regression suite without live model or Telegram requests. No HA
integration changes are needed.
