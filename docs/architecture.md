# Architecture

Multi-event conference platform for PyCon DE / PyData Berlin. Claude Code: read this when the user
asks about how apps fit together, what an app owns, or about external integrations.

## Django apps

- **event_talks/** - project config (settings, URLs, ASGI/WSGI). All settings via env vars, see
  `django-vars.env` for the reference list.
- **events/** - `Event` model for multi-conference support. Each event has its own slug, branding,
  talks, and users.
- **talks/** - core domain: Talk, Speaker, Room, Rating, SavedTalk, Question, Livestream, VideoLink.
  Also holds management commands and template tags.
- **users/** - custom email-based user (no username). Passwordless login via email codes
  (django-allauth). Optional Discord OAuth with guild role mapping (`DISCORD_ROLES` env var).
- **utils/** - email hashing, URL helpers.

## Non-obvious bits

- **Pretalx access** goes through [pytanis](https://pypi.org/project/pytanis/), not the raw Pretalx
  API.
- **Tailwind CSS v4** uses the standalone binary, downloaded by `scripts/dev-setup.sh`.
- **SVG icons** live in `svg/` and load via a template tag, not `<img src=>`.
- **Logging** uses structlog with JSON output. Emails are hashed (see `utils/email_utils.py`) for
  privacy in log records.
