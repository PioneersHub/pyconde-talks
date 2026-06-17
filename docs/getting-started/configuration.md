---
icon: lucide/settings
---

# Configuration

All runtime settings are controlled through environment variables. There is no separate
`settings_local.py`:
[`event_talks/settings.py`](https://github.com/PioneersHub/pyconde-talks/blob/main/event_talks/settings.py)
reads each value from the environment (via [django-environ]) and applies a sensible default when the
variable is unset.

## How settings are loaded

The project follows the [12-factor](https://12factor.net/config) convention: operating system
environment variables always win. On top of that, the app can optionally read variables from a file.

- `DJANGO_READ_VARS_FILE` (default `False`). When `True`, settings are also read from
    [`django-vars.env`](https://github.com/PioneersHub/pyconde-talks/blob/main/django-vars.env) in
    the project root. OS environment variables still take precedence over file values.
- `scripts/dev-setup.sh` sets this automatically: `True` when `django-vars.env` is present, `False`
    otherwise. So a normal local checkout reads from the committed file.

```python
READ_VARS_FILE = env.bool("DJANGO_READ_VARS_FILE", default=False)
if READ_VARS_FILE:
    environ.Env.read_env(BASE_DIR / "django-vars.env")
```

!!! danger "Keep django-vars.env and docker/.env in sync"

    There are two environment files. `django-vars.env` drives local development; `docker/.env` drives
    the Docker Compose production stack. When you add, rename, or remove a variable in one, you must
    make the matching change in the other in the same commit. They are intentionally kept aligned so a
    setting that exists in dev is not silently missing in production.

!!! warning "Never commit real secrets"

    `django-vars.env` and `docker/.env` are tracked, so they may only contain placeholders or safe local
    defaults. The committed `DJANGO_SECRET_KEY` is an obvious dev-only placeholder and must be replaced
    in production. Real tokens (Pretalx, Vimeo, Mailgun, Discord) and the real secret key belong only in
    the deployment environment, never in a commit.

In the tables below, "Default" is the value `settings.py` falls back to when the variable is unset.
Where the committed `django-vars.env` differs (because it targets local development), that is noted.

## Core Django

| Variable                 | Default             | Purpose                                                                         |
| ------------------------ | ------------------- | ------------------------------------------------------------------------------- |
| `DJANGO_DEBUG`           | `False`             | Debug mode. `django-vars.env` sets `True` for local development.                |
| `DJANGO_SECRET_KEY`      | `unsafe-secret-key` | Cryptographic signing key. Must be replaced in production.                      |
| `DJANGO_ALLOWED_HOSTS`   | empty               | Comma-separated hosts Django will serve. Dev file sets `localhost,...`.         |
| `SITE_ID`                | `1`                 | `django.contrib.sites` site id.                                                 |
| `LANGUAGE_CODE`          | `en`                | Default/source language. One of `settings.LANGUAGES` (`en`, `pt-br`).           |
| `TIME_ZONE`              | `Europe/Berlin`     | Project time zone.                                                              |
| `USE_I18N`               | `True`              | Enable Django translation machinery.                                            |
| `USE_TZ`                 | `True`              | Store datetimes as timezone-aware UTC.                                          |
| `LANGUAGE_COOKIE_SECURE` | `True`              | Mark the `django_language` cookie Secure. Set `False` for local dev (no HTTPS). |
| `DJANGO_ADMIN_URL`       | `admin/`            | Path prefix for the Django admin site.                                          |
| `ADMIN_NAMES`            | `Admin`             | Comma-separated admin display names (zipped with `ADMIN_EMAILS`).               |
| `ADMIN_EMAILS`           | `admin@example.com` | Comma-separated admin emails. Used as `ADMINS` / `MANAGERS`.                    |

## Static and media files

| Variable                      | Default                 | Purpose                                                       |
| ----------------------------- | ----------------------- | ------------------------------------------------------------- |
| `DJANGO_STATIC_URL`           | `static/`               | URL prefix for static assets.                                 |
| `DJANGO_STATIC_ROOT`          | `<project>/staticfiles` | Where `collectstatic` writes files.                           |
| `DJANGO_STATICFILES_DIRS`     | `<project>/static`      | Extra source directories for static files.                    |
| `DJANGO_STATICFILES_STORAGE`  | `StaticFilesStorage`    | Set to `ManifestStaticFilesStorage` in prod for hashed names. |
| `DJANGO_DEFAULT_FILE_STORAGE` | `FileSystemStorage`     | Default file storage backend.                                 |
| `DJANGO_MEDIA_URL`            | `/media/`               | URL prefix for uploaded/generated media.                      |
| `DJANGO_MEDIA_ROOT`           | `media`                 | Directory for media files (resolved under the project root).  |
| `DJANGO_SERVE_STATIC_LOCALLY` | `False`                 | Let Django serve static/media when running without Nginx.     |

## Database

The database is configured from a single connection URL.

| Variable                    | Default                | Purpose                                                                         |
| --------------------------- | ---------------------- | ------------------------------------------------------------------------------- |
| `DATABASE_URL`              | `sqlite:///db.sqlite3` | Connection string. Use a `postgres://...` URL in production.                    |
| `DJANGO_CONN_MAX_AGE`       | `60`                   | Seconds to keep a persistent connection open. Ignored when the pool is enabled. |
| `DJANGO_CONN_HEALTH_CHECKS` | `True`                 | Detect and replace stale persistent connections.                                |
| `DJANGO_DB_POOL`            | `False`                | Postgres + psycopg3 native connection pool. Leave off on SQLite and under ASGI. |

!!! note "Connection pool vs `CONN_MAX_AGE`"

    These are mutually exclusive. With `DJANGO_DB_POOL=True` (Postgres only), Django manages a pool and
    `DJANGO_CONN_MAX_AGE` is ignored. The default keeps a single connection warm per worker for up to
    `CONN_MAX_AGE` seconds, which works on any backend including SQLite.

## Email

Login codes and admin notifications are sent by email. In development this points at Mailpit; in
production it uses Mailgun via [django-anymail].

| Variable                | Default                                       | Purpose                                                      |
| ----------------------- | --------------------------------------------- | ------------------------------------------------------------ |
| `DJANGO_EMAIL_BACKEND`  | `django.core.mail.backends.smtp.EmailBackend` | Email backend. Set to the Mailgun Anymail backend in prod.   |
| `EMAIL_HOST`            | `localhost`                                   | SMTP host (Mailpit in dev).                                  |
| `EMAIL_PORT`            | `1025`                                        | SMTP port. The setup script points dev at Mailpit on `1026`. |
| `EMAIL_TIMEOUT`         | `10`                                          | SMTP socket timeout in seconds.                              |
| `DEFAULT_FROM_EMAIL`    | `webmaster@localhost`                         | Default `From` address.                                      |
| `SERVER_EMAIL`          | `root@localhost`                              | `From` address for error mail to admins.                     |
| `MAILGUN_API_KEY`       | empty                                         | Mailgun API key (required when the Anymail backend is used). |
| `MAILGUN_API_URL`       | `https://api.eu.mailgun.net/v3`               | Mailgun API base URL.                                        |
| `MAILGUN_SENDER_DOMAIN` | empty                                         | Mailgun sending domain (required with the Anymail backend).  |

!!! note "Mailgun settings are only read when the Anymail backend is active"

    The `MAILGUN_*` variables are only consulted when `DJANGO_EMAIL_BACKEND` is set to
    `anymail.backends.mailgun.EmailBackend`. With the default SMTP backend they are ignored.

## Authentication and accounts

These tune the passwordless email-code login. See [Authentication](authentication.md) for the full
flow.

| Variable                        | Default | Purpose                                                                 |
| ------------------------------- | ------- | ----------------------------------------------------------------------- |
| `LOGIN_REDIRECT_URL`            | `home`  | Where to send users after a successful login.                           |
| `ACCOUNT_LOGIN_BY_CODE_TIMEOUT` | `300`   | Seconds a login code stays valid.                                       |
| `ACCOUNT_EMAIL_SUBJECT_PREFIX`  | empty   | Prefix added to account email subjects.                                 |
| `ALLAUTH_TRUSTED_PROXY_COUNT`   | `0`     | Number of trusted reverse proxies. Set to `1` behind the bundled Nginx. |

!!! warning "Set the proxy count correctly behind a proxy"

    `ALLAUTH_TRUSTED_PROXY_COUNT` must reflect how many trusted proxy hops sit in front of the app. If
    it stays `0` behind Nginx, every request looks like `127.0.0.1`, so allauth's per-IP rate limits
    collapse into one global bucket and a single attacker can lock everyone out. The Docker/Nginx
    deployment sets it to `1`.

## Email validation API

A new attendee's email is checked against an external validation API to confirm a ticket purchase
before an account is created. An event can define its own `validation_api_url`; otherwise the
fallback below is used.

| Variable                                    | Default        | Purpose                                                                |
| ------------------------------------------- | -------------- | ---------------------------------------------------------------------- |
| `EMAIL_VALIDATION_API_URL_FALLBACK`         | empty          | Validation API URL used when an event does not define its own.         |
| `EMAIL_VALIDATION_API_TIMEOUT`              | `10`           | Timeout in seconds for validation API calls.                           |
| `AUTHORIZED_EMAILS_WHITELIST`               | `ADMIN_EMAILS` | Emails that bypass API validation entirely.                            |
| `EMAIL_VALIDATION_API_OAUTH2_CLIENT_ID`     | empty          | OAuth2 client id for authenticated validation calls.                   |
| `EMAIL_VALIDATION_API_OAUTH2_CLIENT_SECRET` | empty          | OAuth2 client secret.                                                  |
| `EMAIL_VALIDATION_API_OAUTH2_TOKEN_URL`     | empty          | OAuth2 token endpoint. All three OAuth2 values are required to enable. |

## Discord OAuth

Optional login with Discord, gated by guild membership and role. See
[Authentication](authentication.md) for how roles map to permissions.

| Variable                | Default | Purpose                                                         |
| ----------------------- | ------- | --------------------------------------------------------------- |
| `DISCORD_CLIENT_ID`     | empty   | Discord OAuth application client id.                            |
| `DISCORD_CLIENT_SECRET` | empty   | Discord OAuth application client secret.                        |
| `DISCORD_GUILD_ID`      | empty   | Numeric id of the Discord server checked for membership.        |
| `DISCORD_API_TIMEOUT`   | `5`     | Timeout in seconds for Discord API calls.                       |
| `DISCORD_ROLES`         | `{}`    | JSON map of role name to role id (e.g. `{"attendee":"BBBBB"}`). |
| `DISCORD_ALLOWED_ROLES` | empty   | Role names permitted to log in. Empty means no Discord access.  |
| `DISCORD_ADMIN_ROLES`   | empty   | Role names that grant `is_superuser` and `is_staff`.            |
| `DISCORD_STAFF_ROLES`   | empty   | Role names that grant `is_staff` only.                          |

!!! warning "An empty `DISCORD_ALLOWED_ROLES` rejects all Discord logins"

    Discord access is opt-in. With no allowed roles configured, every Discord login is rejected. Roles
    in `DISCORD_ROLES` must use the same names referenced by the allowed/admin/staff lists.

## Pretalx sync

Talk import from the Pretalx REST API. See [Pretalx sync](../reference/pretalx-sync.md) for the
importer modes and workflow.

| Variable                    | Default                | Purpose                                                                        |
| --------------------------- | ---------------------- | ------------------------------------------------------------------------------ |
| `PRETALX_API_TOKEN`         | empty                  | API token used by `import_pretalx_talks`.                                      |
| `PICKLE_PRETALX_TALKS`      | `False`                | Cache fetched Pretalx data to disk to speed up repeated imports.               |
| `PRETALX_DIGEST_RECIPIENTS` | falls back to `ADMINS` | Recipients of the detect-only summary email. Set to `-` to disable the digest. |

## Livestreams and video

| Variable                     | Default       | Purpose                                                   |
| ---------------------------- | ------------- | --------------------------------------------------------- |
| `LIVESTREAMS_SHEET_ID`       | empty         | Google Sheet id used by `import_livestream_urls`.         |
| `LIVESTREAMS_WORKSHEET_NAME` | `Livestreams` | Worksheet name within that sheet.                         |
| `VIMEO_ACCESS_TOKEN`         | empty         | Vimeo API token used by `update_video_links`.             |
| `VIMEO_PROJECT_IDS`          | empty         | Comma-separated Vimeo project ids to scan for recordings. |

## Social cards

Talk social cards are rendered with a configurable font.

| Variable              | Default                     | Purpose                                       |
| --------------------- | --------------------------- | --------------------------------------------- |
| `TALK_CARD_FONT`      | `assets/fonts/NotoSans.ttf` | Path to the TrueType font used for card text. |
| `TALK_CARD_FONT_NAME` | `Noto Sans`                 | Font family name reported in card metadata.   |

## Logging

| Variable                    | Default          | Purpose                                                            |
| --------------------------- | ---------------- | ------------------------------------------------------------------ |
| `LOG_LEVEL`                 | `INFO`           | Level for the `event_talks`, `users`, and `talks` loggers.         |
| `AUTH_LOG_LEVEL`            | `INFO`           | Level for the dedicated `auth` logger (writes to `auth.log`).      |
| `DJANGO_DATABASE_LOG_LEVEL` | `ERROR`          | Level for `django.db.backends`.                                    |
| `DJANGO_LOGS_DIR`           | `<project>/logs` | Directory for rotating JSON log files.                             |
| `LOG_EMAIL_HASH`            | `True`           | Hash email addresses in logs instead of writing them in the clear. |

!!! info "Email privacy in logs"

    With `LOG_EMAIL_HASH=True`, emails are recorded as a SHA-256 hash rather than plaintext, so log
    files do not leak attendee addresses. This is described further in
    [Authentication](authentication.md).

## Error tracking (Sentry)

Sentry is only initialized when a DSN is set, so dev and test stay quiet.

| Variable                    | Default                                  | Purpose                                                       |
| --------------------------- | ---------------------------------------- | ------------------------------------------------------------- |
| `SENTRY_DSN`                | empty                                    | Sentry project DSN. Leave blank to disable Sentry.            |
| `SENTRY_ENVIRONMENT`        | `production` (or `development` if debug) | Environment tag reported to Sentry.                           |
| `SENTRY_TRACES_SAMPLE_RATE` | `0.0`                                    | Performance trace sampling rate (errors are always captured). |
| `SENTRY_SEND_DEFAULT_PII`   | `False`                                  | Whether to send user identifiers. Off by default for privacy. |

## Security and cookies

These default to safe production values, so the local `django-vars.env` relaxes them for HTTP
development.

| Variable                         | Default                             | Purpose                                                            |
| -------------------------------- | ----------------------------------- | ------------------------------------------------------------------ |
| `SESSION_COOKIE_SECURE`          | `True`                              | Send the session cookie over HTTPS only. Dev file sets `False`.    |
| `CSRF_COOKIE_SECURE`             | `True`                              | Send the CSRF cookie over HTTPS only. Dev file sets `False`.       |
| `DJANGO_CSRF_TRUSTED_ORIGINS`    | `http://localhost,http://127.0.0.1` | Origins trusted for CSRF.                                          |
| `SECURE_HSTS_SECONDS`            | `31536000`                          | HSTS max-age (1 year). Dev file sets `0` to disable HSTS.          |
| `SECURE_HSTS_INCLUDE_SUBDOMAINS` | `True`                              | Apply HSTS to subdomains.                                          |
| `SECURE_HSTS_PRELOAD`            | `False`                             | Opt in to the HSTS preload list. Off by default.                   |
| `DATA_UPLOAD_MAX_NUMBER_FIELDS`  | `3000`                              | Max form fields per request (raised to manage many users at once). |

Other security headers (`X-Frame-Options: DENY`, content-type nosniff, referrer policy, the
cross-origin opener policy, and the `SECURE_PROXY_SSL_HEADER`) are set in code and are not
environment-configurable.

## Feature flags and event behaviour

| Variable                        | Default | Purpose                                                                |
| ------------------------------- | ------- | ---------------------------------------------------------------------- |
| `DEFAULT_EVENT`                 | empty   | Slug of the event pre-selected on the login page and linked on signup. |
| `SHOW_UPCOMING_TALKS_LINKS`     | `False` | Show links to upcoming talks. Dev file sets `True`.                    |
| `IMPORT_TALKS_WITHOUT_SPEAKERS` | `True`  | Allow Pretalx import of talks that have no speakers yet.               |
| `CHAIR_ROOM_TRANSITION_MINUTES` | `5`     | Warn session chairs about room transitions tighter than this.          |

### Multi-event behaviour

This instance can host several conferences at once. Each `Event` has its own slug, branding, talks,
and validation API URL, and users are linked to the events they have access to.

- `DEFAULT_EVENT` is the slug shown pre-selected on the login page. The current default is
    `pyconde-pydata-2026`. New users authorizing via Discord are linked to this event, and the login
    view offers it as the default selection.
- A regular user only sees the active events they are linked to. Superusers see all active events.
- The committed dev defaults set `DEFAULT_EVENT=pyconde-pydata-2026`, and `dev-setup.sh` creates the
    `pyconde-pydata-2025`, `pyconde-pydata-2026`, and `pydata-berlin-2026` sample events.

For how events relate to talks, users, and the rest of the data model, see
[Architecture](../architecture/index.md).

[django-anymail]: https://anymail.dev/
[django-environ]: https://django-environ.readthedocs.io/
