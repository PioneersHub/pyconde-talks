---
icon: lucide/plug
---

# Integrations

The application talks to several external services. Most are reached by management commands run on a
schedule; a couple (email, Discord OAuth) run inline during a request. Every credential comes from
an environment variable, so nothing is hard-coded.

## Pretalx

Pretalx is the source of truth for talks, speakers, rooms, and the schedule. The application reads
from the Pretalx REST API through a small in-repo client rather than a third-party wrapper, so only
the fields the importer actually uses are modeled and there is no heavyweight dependency to keep in
sync.

The client lives at
[`talks/management/commands/_pretalx/client.py`](https://github.com/PioneersHub/pyconde-talks/blob/main/talks/management/commands/_pretalx/client.py)
and is built on `httpx2` and `pydantic`:

- `PretalxClient` implements only the two calls the importer needs: `submissions()` (paginated,
    validated) and `event()` (a single object). Requests carry the API token (`PRETALX_API_TOKEN`)
    and a `Pretalx-Version` header.
- Requests are throttled with a simple monotonic-clock rate limiter (default 2 calls per second)
    because Pretalx rate-limits aggressively. The live fetch is wrapped in exponential-backoff retry
    (via `tenacity`) for transient transport and HTTP-status errors.
- A submission that fails schema validation is skipped with a warning instead of aborting the whole
    import, so one malformed record cannot block a sync.
- A dev-only on-disk cache (`PICKLE_PRETALX_TALKS`) short-circuits the network call when enabled, so
    repeated local runs do not hammer the API.

The importer supports a detect-and-review mode that records diffs as
[`PendingPretalxChange`](data-model.md#pendingpretalxchange) rows for an admin to apply or dismiss.

!!! tip "Operator manual"

    For the import modes, change detection, image regeneration, the detect-and-review workflow,
    scheduling, and the module layout, see the [Pretalx sync reference](../reference/pretalx-sync.md).

## Email

Email backs the passwordless login flow (one-time codes) and notification messages such as the
Pretalx detect digest. The backend is chosen by `DJANGO_EMAIL_BACKEND`, so the same code runs in
both environments.

=== "Development (Mailpit)"

    Local setup downloads and runs [Mailpit](https://github.com/axllent/mailpit), which captures every
    outgoing message in a web inbox instead of delivering it. The default SMTP backend
    (`django.core.mail.backends.smtp.EmailBackend`) points at `EMAIL_HOST` / `EMAIL_PORT`.

    Mailpit's default SMTP port (1025) collides with apps like Proton Mail Bridge, so the dev setup
    binds a dedicated IPv4 SMTP port (1026 by default) and points Django at it. Open the Mailpit UI
    (port 8025 by default) to read the login codes.

=== "Production (Mailgun)"

    Production sets `DJANGO_EMAIL_BACKEND=anymail.backends.mailgun.EmailBackend`. When that backend is
    active, `settings.py` reads the Mailgun credentials:

    - `ANYMAIL_MAILGUN_API_KEY` from `MAILGUN_API_KEY`.
    - `ANYMAIL_MAILGUN_API_URL` from `MAILGUN_API_URL` (defaults to the EU endpoint,
        `https://api.eu.mailgun.net/v3`).
    - `ANYMAIL_MAILGUN_SENDER_DOMAIN` from `MAILGUN_SENDER_DOMAIN`.

    Delivery goes through [django-anymail](https://anymail.dev/). `DEFAULT_FROM_EMAIL` and
    `SERVER_EMAIL` set the sender addresses.

## Video: Vimeo and YouTube

Talks carry a `video_link`. Two providers are supported, identified by URL.

**Vimeo** recordings are imported by the `update_video_links` command
([source](https://github.com/PioneersHub/pyconde-talks/blob/main/talks/management/commands/update_video_links.py)).
It calls the Vimeo API (`https://api.vimeo.com/me/projects/{project_id}/videos`) with a bearer
token, paginates through each configured project folder, and reads each video's `name` and
`player_embed_url`. The command expects the video name to start with the Pretalx code, in the form
`{pretalx_id}-{title}`, and matches it to the talk whose `pretalx_code` equals that prefix. The
match is exact, not a substring, and an ambiguous match is skipped rather than risk overwriting the
wrong talk's link. Configuration: `VIMEO_ACCESS_TOKEN` and a comma-separated `VIMEO_PROJECT_IDS`.
Pass `--dry-run` to preview without writing.

**YouTube** videos are embedded directly. When a YouTube link is saved on a talk, the model appends
`enablejsapi=1` to the URL (idempotently) so the embedded player can be controlled from JavaScript.
The `video_provider` property normalizes both `youtube.com` and `youtu.be` links to the single name
"Youtube" for templates.

## Google Sheets livestream import

Per-room live stream embed URLs are maintained in a Google Sheet and imported by the
`import_livestream_urls` command
([source](https://github.com/PioneersHub/pyconde-talks/blob/main/talks/management/commands/import_livestream_urls.py)).
It downloads the sheet as XLSX
(`https://docs.google.com/spreadsheet/ccc?key={sheet_id}&output=xlsx`), reads it with `pandas`,
keeps only Vimeo rows that have an embed link and a valid start/end time, and replaces the existing
`Streaming` rows for the target event.

The import is scoped by `--event-slug` (defaulting to `DEFAULT_EVENT`). This matters because rooms
are event-scoped and the import deletes existing streamings before recreating them: with a slug it
only clears that event's streamings, so importing one event's sheet can never wipe another's. A slug
that does not resolve aborts the command rather than silently widening the delete. Configuration:
`LIVESTREAMS_SHEET_ID` and `LIVESTREAMS_WORKSHEET_NAME` (defaults to "Livestreams"). Times are
localized to `Europe/Berlin`, with malformed cells coerced to "not a time" and dropped.

## Discord OAuth

Discord is an optional social login provider (via django-allauth) with guild-role mapping: a user's
Discord roles in the configured guild can grant access or staff/admin flags. The requested scopes
are `identify`, `email`, and `guilds.members.read`, and the role mapping is driven by
`DISCORD_GUILD_ID`, `DISCORD_ROLES`, `DISCORD_ALLOWED_ROLES`, `DISCORD_ADMIN_ROLES`, and
`DISCORD_STAFF_ROLES`.

See [Authentication](../getting-started/authentication.md) for the full login flow, including how
Discord-only users add an email address.

## Health checks

The unauthenticated endpoint `/ht/` reports liveness, backed by
[django-health-check](https://github.com/revsys/django-health-check) (`health_check` in
`INSTALLED_APPS`). It is wired up in
[`event_talks/urls.py`](https://github.com/PioneersHub/pyconde-talks/blob/main/event_talks/urls.py)
and every Docker and deploy liveness probe hits it, so it stays cheap and self-contained. The
configured checks are cache, database, storage, and (via `psutil`) disk and memory.

!!! warning "The mail check is deliberately excluded"

    The endpoint does not include the mail check. That check opens a real SMTP/Mailgun connection on
    every hit, which would let anyone drive outbound mail-backend connections and would flip the
    container to "unhealthy" during an unrelated email-provider outage, triggering false deploy
    rollbacks. Monitor mail deliverability separately.

## Structured logging

Logging uses [structlog](https://www.structlog.org/) on top of Python's logging. The console handler
renders colored output for development; file handlers write JSON to rotating logs under the logs
directory (`django.log`, `error.log`, and a dedicated `auth.log` for authentication events). Per-app
loggers (`event_talks`, `users`, `talks`, `auth`) have their levels controlled by `LOG_LEVEL` and
`AUTH_LOG_LEVEL`.

Email addresses are never logged in clear text. `utils/email_utils.py` provides `hash_email()` (a
SHA-256 hex digest) and `obfuscate_email()` (a masked form for display), and `LOG_EMAIL_HASH`
(default on) keeps log records using the hashed form. The same privacy stance carries over to
Sentry: when `SENTRY_DSN` is set, `send_default_pii` defaults to off so user identifiers are not
sent unless explicitly opted in.
