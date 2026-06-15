---
icon: lucide/home
---

# Conference Talks Website

A [Django](https://www.djangoproject.com/) 6 application that publishes talks, schedules, and live
Q&A for conference events such as [PyCon DE](https://pycon.de/) and
[PyData Berlin](https://berlin.pydata.org/). One installation serves multiple events at once, each
with its own branding, talks, and users.

This codebase powers the public sites [talks.pycon.de](https://talks.pycon.de) and
[videos.pydata-berlin.org](https://videos.pydata-berlin.org).

## Features

- **Multi-event support** - manage multiple conferences from a single instance, each with its own
    branding, talks, and users
- **Talk management** - import talks from [Pretalx](https://pretalx.com/), browse by
    room/track/date, full-text search
- **Schedule** - grid view organized by room and time slot
- **Ratings** - attendees can rate talks (1-5 stars) with optional comments, visible only to admins
- **Q&A** - attendees ask and vote on questions; moderators approve, reject, or mark them answered
- **Saved talks** - bookmark talks for quick access later
- **Live streaming** - embed Vimeo/YouTube streams per room with automatic detection
- **Social cards** - auto-generated talk images with speaker avatars for sharing on social media
- **Passwordless login** - email-based login codes; no passwords for regular users
- **Discord OAuth** - optional login via Discord with role-based access control
- **Dark mode** - class-based toggle with [Tailwind CSS](https://tailwindcss.com/) v4
- **HTMX** - dynamic dashboard, ratings, Q&A voting, and partial page updates without full reloads
- **Structured logging** - JSON logs with rotating file handlers and email privacy (hashed emails)
- **Health checks** - `/ht/` endpoint for monitoring

## Tech stack

| Layer        | Technology                                                                                      |
| ------------ | ----------------------------------------------------------------------------------------------- |
| Language     | [Python](https://www.python.org/) 3.14                                                          |
| Framework    | [Django](https://www.djangoproject.com/) 6                                                      |
| ASGI server  | [Daphne](https://github.com/django/daphne)                                                      |
| Auth         | [django-allauth](https://docs.allauth.org/) (email codes + Discord OAuth)                       |
| Frontend     | [Tailwind CSS](https://tailwindcss.com/) v4, [HTMX](https://htmx.org/)                          |
| Database     | SQLite (dev), [PostgreSQL](https://www.postgresql.org/) 18 (prod)                               |
| Email        | [Mailpit](https://mailpit.axllent.org) (dev), [Mailgun](https://www.mailgun.com/) (prod)        |
| Logging      | [structlog](https://www.structlog.org/) + django-structlog                                      |
| Talks import | [Pretalx](https://pretalx.com/) REST API                                                        |
| Video        | Vimeo API, YouTube embeds                                                                       |
| Deployment   | [Docker](https://www.docker.com/) multi-stage build, [Nginx](https://nginx.org/), Let's Encrypt |
| Package mgr  | [uv](https://docs.astral.sh/uv)                                                                 |

## Where to go next

- **[Getting started](getting-started/index.md)** - set up a local development environment with one
    command, log in with test users, and explore the app.
- **[Architecture](architecture/index.md)** - how the Django apps fit together: events, talks,
    users, and the external integrations (Pretalx, Vimeo, Discord, Mailgun).
- **[Features](features/talks.md)** - a tour of what the site does for attendees and organizers,
    starting with talks and the schedule.
- **[Development](development/index.md)** - day-to-day commands, testing, code quality tools, and
    running the full Docker stack locally.
- **[Reference](reference/management-commands.md)** - management commands for importing talks,
    livestream URLs, video links, and generating fake data.
- **[Deployment](deployment/index.md)** - the tag-driven CI/CD pipeline, shared GHCR images, and
    per-site server setup.

!!! tip "In a hurry?"

    The quickest way to see the app running is the one-command dev setup described in
    [Getting started](getting-started/index.md). It creates test users, fake talks, and starts the
    server on port 8000.

Curious how this project came about? Read [the story](story.md) behind it, from a streaming problem
at PyCon DE 2025 to the app that runs today.

Want to help out? See the [contributing guide](contributing.md). The project is open source under
the [MIT license](license.md).
