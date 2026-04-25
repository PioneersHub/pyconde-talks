# Conference Talks Website

A Django application to publish talks, schedules, and live Q&A for conference events such as
[PyCon DE](https://pycon.de/) and [PyData Berlin](https://berlin.pydata.org/).

## Features

- **Multi-event support** - manage multiple conferences from a single instance, each with its own
  branding, talks, and users
- **Talk management** - import talks from [Pretalx], browse by room/track/date, full-text search
- **Schedule** - grid view organized by room and time slot
- **Ratings** - attendees can rate talks (1-5 stars) with optional comments (only visible to admins)
- **Q&A** - attendees can ask and vote on questions; moderators can approve, reject, or mark them as
  answered
- **Saved talks** - bookmark talks for quick access later
- **Live streaming** - embed Vimeo/YouTube streams per room with automatic detection
- **Social cards** - auto-generated talk images with speaker avatars for sharing on social media
- **Passwordless login** - email-based login codes; no passwords for regular users
- **Discord OAuth** - optional login via Discord with role-based access control
- **Dark mode** - class-based toggle with [Tailwind CSS] v4
- **HTMX** - dynamic dashboard, ratings, Q&A voting, and partial page updates without full reloads
- **Structured logging** - JSON logs with rotating file handlers and email privacy (hashed emails)
- **Health checks** - `/ht/` endpoint for monitoring

## Tech stack

| Layer        | Technology                                             |
| ------------ | ------------------------------------------------------ |
| Language     | [Python] 3.14                                          |
| Framework    | [Django] 6                                             |
| ASGI server  | [Daphne]                                               |
| Auth         | [django-allauth] (email codes + Discord OAuth)         |
| Frontend     | [Tailwind CSS] v4, [HTMX]                              |
| Database     | SQLite (dev), [PostgreSQL] 18 (prod)                   |
| Email        | [Mailpit] (dev), [Mailgun] (prod) via [django-anymail] |
| Logging      | [structlog] + [django-structlog]                       |
| Talks import | [Pretalx] API via [pytanis]                            |
| Video        | Vimeo API, YouTube embeds                              |
| Deployment   | [Docker] multi-stage build, [Nginx], Let's Encrypt     |
| Package mgr  | [uv]                                                   |

## Project structure

```
pyconde-talks/
├── event_talks/            # Django project - settings, URLs, ASGI/WSGI
├── events/                 # Event model and admin
├── talks/                  # Talks, speakers, rooms, ratings, Q&A, streaming
│   ├── management/commands/  # import_pretalx_talks, import_livestream_urls,
│   │                         # update_video_links, generate_fake_talks
│   ├── templatetags/         # highlight, ratings, schedule, SVG, time filters
│   └── tests/
├── users/                  # Custom user model, passwordless login, Discord adapter
├── utils/                  # Email hashing, URL helpers
├── templates/              # Django templates (base, home, talks, users, account)
├── assets/css/             # Tailwind CSS source (input.css)
├── scripts/                # Project automation scripts (including dev-setup.sh)
├── static/                 # Compiled CSS/JS and per-event images
├── svg/                    # SVG icons loaded via template tag
├── docker/                 # Dockerfile, compose.yaml, entrypoint, backup scripts
├── nginx/                  # Example Nginx config with rate limiting and caching
├── .devcontainer/          # VS Code Dev Container / GitHub Codespaces
├── django-vars.env         # Environment variables (template)
└── pyproject.toml          # Dependencies and tool configuration
```

## Configuration

All settings are controlled through environment variables. The file `django-vars.env` lists every
variable with its default value. Set `DJANGO_READ_VARS_FILE=true` to load values from that file, or
export them as environment variables.

Key variables:

| Variable                    | Description                          | Default                |
| --------------------------- | ------------------------------------ | ---------------------- |
| `DJANGO_DEBUG`              | Enable debug mode                    | `True`                 |
| `DATABASE_URL`              | Database connection string           | `sqlite:///db.sqlite3` |
| `DEFAULT_EVENT`             | Active event slug                    | `pyconde-pydata-2026`  |
| `DJANGO_SECRET_KEY`         | Secret key (change in production)    | insecure default       |
| `DJANGO_ALLOWED_HOSTS`      | Comma-separated allowed hosts        | `localhost,127.0.0.1`  |
| `EMAIL_HOST` / `EMAIL_PORT` | SMTP server for dev (Mailpit)        | `localhost:1025`       |
| `MAILGUN_API_KEY`           | Mailgun API key for production email | empty                  |
| `PRETALX_API_TOKEN`         | Pretalx API token for talk imports   | empty                  |
| `VIMEO_ACCESS_TOKEN`        | Vimeo token for video link updates   | empty                  |
| `DISCORD_CLIENT_ID`         | Discord OAuth client ID              | empty                  |
| `DISCORD_GUILD_ID`          | Discord server ID for role checks    | empty                  |

See `django-vars.env` for the full list, including Discord roles, logging, security cookies,
livestream sheet IDs, and feature flags.

## Development

### Quick start

The fastest way to get a running development environment is to open the project in a
[Dev Container](https://containers.dev/) (VS Code or GitHub Codespaces). The container runs
`dev-setup.sh` automatically after creation.

To set up locally instead, run:

```bash
RUN_SERVER=true PRETALX_SYNC=false IMPORT_STREAMS=false GEN_FAKE_DATA=true \
  scripts/dev-setup.sh
```

This script will:

1. Install [uv] (if not present) and create a `.venv` virtual environment
2. Install all Python dependencies
3. Download [Tailwind CSS] standalone and start the watcher
4. Download the Noto Sans font (used for social card generation)
5. Run database migrations
6. Create test users: `user1@example.com`, `user2@example.com`, and `admin@example.com`
7. Generate fake talks, rooms, speakers, and streaming data
8. Start [Mailpit] on port **8025** (for email testing)
9. Start the Django development server on port **8000**

### Authentication in development

Regular users log in with email codes (passwordless):

1. Open http://127.0.0.1:8000
2. Enter `user1@example.com` or `user2@example.com`
3. Open http://localhost:8025 (Mailpit) and copy the login code
4. Paste the code into the form

Admin users can also log in with a password:

1. Open http://127.0.0.1:8000/admin/
2. Email: `admin@example.com`, password: `admin`

### Management commands

```bash
# Import talks from Pretalx
uv run python manage.py import_pretalx_talks \
  --pretalx-event-url https://pretalx.com/my-event/ \
  --event-slug my-event \
  --api-token TOKEN

# Import livestream URLs from a Google Sheet
uv run python manage.py import_livestream_urls \
  --livestreams-sheet-id SHEET_ID \
  --livestreams-worksheet-name Sheet1

# Update video links from Vimeo
uv run python manage.py update_video_links \
  --vimeo-access-token TOKEN \
  --vimeo-project-ids 123,456

# Generate fake data for development
uv run python manage.py generate_fake_talks
```

All import commands support `--dry-run` to preview changes without writing to the database.

## Testing

The project uses [pytest] with random test ordering and coverage reports:

```bash
uv run pytest
```

Coverage output is written to the terminal and to `lcov.info` (LCOV format). The test suite covers
models, views, admin, management commands, template tags, validators, and utilities.

## Code quality

```bash
# Linting
uv run ruff check .

# Formatting
uv run ruff format .

# Type checking
zuban check

# Template linting
uv run djlint templates/ --lint

# Security scanning
uv run bandit -r . -c pyproject.toml
```

Tools are configured in `pyproject.toml`:

- **ruff** - linting and formatting (100-char line length, Python 3.14, all rules enabled)
- **zuban (mypy)** - strict mode with django-stubs
- **djlint** - Django template linting
- **bandit** - security analysis
- **coverage** - omits migrations, settings, and ASGI/WSGI files

## Deployment

The `docker/` directory contains everything needed for a production deployment with Docker,
PostgreSQL, and Nginx.

### Build

```bash
cd docker
docker buildx bake --allow=fs.read=.. --set '*.args.APP_DOMAIN=talks.example.com'
```

This builds two targets:

1. **django** - multi-stage image with a non-root user (UID 10000), health check on `/ht/`, and
   Daphne as the ASGI server
2. **staticfiles** - exports compiled static files to `docker/staticfiles/` for serving with Nginx

### Run

```bash
# Prepare directories
sudo mkdir -p ${MEDIA_DIR} ${STATIC_DIR} ${LOGS_DIR}

# Copy static files
mv docker/staticfiles/* ${STATIC_DIR}/

# Set permissions for Nginx (www-data) and Django (UID 10000)
sudo APP_DOMAIN=talks.example.com ./docker/ensure_permissions.sh

# Start PostgreSQL and Django
cd docker
docker compose up -d
```

### Nginx

An example Nginx configuration is in `nginx/talks.example.com`. It includes:

- SSL with Let's Encrypt (Certbot)
- Security headers (CSP, HSTS, X-Frame-Options)
- Gzip compression
- Rate limiting (10 req/s with burst allowances)
- Static file caching (30 days)

```bash
sudo cp nginx/talks.example.com /etc/nginx/sites-available/${APP_DOMAIN}
sudo ln -s /etc/nginx/sites-available/${APP_DOMAIN} /etc/nginx/sites-enabled/
sudo certbot --nginx -d ${APP_DOMAIN}
sudo systemctl reload nginx
```

### Database backup

```bash
./docker/backup_db.sh
```

## License

[MIT](LICENSE)

[Daphne]: https://github.com/django/daphne
[Django]: https://www.djangoproject.com/
[Docker]: https://www.docker.com/
[HTMX]: https://htmx.org/
[Mailgun]: https://www.mailgun.com/
[Mailpit]: https://mailpit.axllent.org
[Nginx]: https://nginx.org/
[PostgreSQL]: https://www.postgresql.org/
[Pretalx]: https://pretalx.com/
[Python]: https://www.python.org/
[Tailwind CSS]: https://tailwindcss.com/
[django-allauth]: https://docs.allauth.org/
[django-anymail]: https://anymail.dev/
[django-structlog]: https://django-structlog.readthedocs.io/
[pytest]: https://docs.pytest.org/
[pytanis]: https://github.com/PioneersHub/pytanis
[structlog]: https://www.structlog.org/
[uv]: https://docs.astral.sh/uv
[tailwindCSS]: https://tailwindcss.com/
[uv]: https://docs.astral.sh/uv
