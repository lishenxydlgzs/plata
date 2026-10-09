# Telegram household onboarding

## Scope and experience

Add Connections → Telegram to the existing private household workspace, plus a
home-page discovery card. One household owner registers a bot with BotFather,
pastes its token, and receives validated connection status. Each adult selects
an existing person (or creates one), generates a 15-minute single-use invitation,
opens its Telegram link or locally generated QR code, and presses Start. Start
consents to the displayed shared-memory policy and requests connection; an owner
confirms the exact Telegram account in the workspace. Invitations can be shared
with a second adult without sharing the bot token or workspace access. Successful
first reply delivery completes onboarding. Text only; voice and home-device
execution are future work.

## API onboarding

The UI and coding agents use the same REST endpoints and state transitions.
Clients on the trusted workspace network send `X-Plata-Workspace: 1` to
`/api/telegram` routes; this is a CSRF/client-intent header, not an API secret.
Non-browser callers do not need an Origin header, cookies, or a browser session.
Use the existing people API to select/create a profile, configure the bot via
`PUT /api/telegram/bot`, create an invitation, inspect its pending numeric user ID,
and confirm through `POST /api/telegram/invitations/{id}/approve`. Status exposes
connection errors and first-reply completion. The same API revokes accounts,
cancels invitations, disconnects the bot, and generates optional local QR SVGs.

The API does not bypass Telegram account ownership: the recipient must open the
invitation and press Start before an authorized owner or coding agent can approve
it. BotFather registration remains an external prerequisite. Read credentials
from a private secret file or hidden terminal input, never command-line arguments
or committed examples. README documents both the UI and complete API lifecycle.

## Authorization and privacy

The existing workspace has no login and is a trusted local-network administrative
surface. These settings inherit that boundary: do not expose the workspace to the
Internet. JSON/custom-header and same-origin checks prevent cross-site browser
requests; they are not a substitute for authentication. Telegram invitations are
random, expiring, stored as hashes, and redeemable only in direct chats. Every
redemption requires owner confirmation, including the first account. Numeric
Telegram user IDs establish authorization; display names are labels, not proof.
Only confirmed accounts can invoke the conversation engine. Disconnect revokes
access and pending output. Changing bots removes invitations and account bindings.

The bot token and Telegram operational state live in an ignored, mode-0600 SQLite
file under a dedicated mode-0700 data directory. No token is returned by APIs,
logged, placed in URLs returned to browsers, or written to repository examples.
Telegram API HTTP logging is suppressed because its URL includes the credential.
QR generation is local, never through a third-party service.

Chat history is separate per connected account. Messages, facts, and reported
learning/behavior events may enter Plata's shared household memory and be visible
in the household workspace. This is explicitly disclosed before inviting and in
Telegram; the feature does not promise private memory. /new starts fresh short-term
history without deleting shared memory. /disconnect revokes the current account.

## Mechanisms

A lifespan-managed long-polling adapter calls the existing MessageRouter directly.
No public webhook, port forwarding, or per-user numeric-ID lookup is needed.
Persist configuration, invitations, account bindings, update offset, inbox, and
outbox. Ingest updates transactionally before advancing offset. Process messages
sequentially. Persist processing status before agent invocation; interrupted turns
receive a retry instruction on restart instead of repeating potentially mutating
agent work. Persist replies before sending and retry temporary transport failures.
Telegram sendMessage has no idempotency key: an ambiguous network failure can
produce duplicate reply delivery, but must not repeat agent execution.

A connection generation owns each worker. Stop/await it before replacing bot
configuration. Serialize management changes and processing so revocation cannot
race agent work or delivery. Persist permission decisions before sending welcome.
Surface unavailable credentials, webhook conflicts, and poll conflicts as readable
status with recovery advice; never automatically remove another bot's webhook.

Agent instructions describe the text channel and the verified sender profile.
Executor validation blocks playback/timer output and state changes for Telegram,
independently of model interpretation. Natural messages always go to the agent;
only explicit transport commands (/start, /help, /new, /disconnect) are handled by
the adapter. Telegram output is plain text, chunked within its UTF-16 length limit.

## Validation and rollout

Mock Telegram transport and agent responses. Cover invitations, expiry/replay,
owner approval, unauthorized/group updates, restarts, duplicate updates, retries,
revocation, token secrecy, and action gating. Run existing backend tests and UI
checks/build; visually check desktop/mobile onboarding using disposable data.
Do not call live Gemini or Telegram in tests. Deployment waits for user review.

## Implementation verification (2026-10-06)

- Backend suite: 170 tests pass, including pairing/replay/expiry, API CSRF and
  credential redaction, restart recovery, delivery retries, account removal,
  Unicode chunking, multiple-worker rejection, and Telegram capability enforcement.
- API-only acceptance: two synthetic household members complete the documented
  HTTP setup/people/invitation/confirmation/status flow without browser sessions;
  premature/replayed approvals fail, and HTTP cancellation/revocation/disconnection
  remove access. README shell and Python examples pass syntax validation.
- Frontend: TypeScript/Vite production build and all 5 existing API tests pass.
- Browser acceptance: two synthetic household members complete setup, invitation,
  owner confirmation, and first reply with mocked Telegram and agent responses.
  Invalid-token recovery and locally generated QR rendering also pass; desktop
  (1440 px) and phone (390 px) have no horizontal overflow or browser exceptions.
- No live bot, Gemini call, or deployment was used for verification. A live bot
  smoke test remains part of the user-approved rollout, using either setup path.
