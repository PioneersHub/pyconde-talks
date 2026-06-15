---
icon: lucide/wrench
---

# Day-to-day development

This page covers the everyday loop: refreshing local state, running the dev server, and finding your
way around the supporting services. It assumes you have already completed the first-time setup with
`scripts/dev-setup.sh`.

## Refreshing local state

`scripts/dev-setup.sh` is idempotent: re-run it whenever you want to refresh migrations, test users,
fake data, or static assets. Environment flags control what it does. The defaults live at the top of
[scripts/dev-setup.sh](https://github.com/PioneersHub/pyconde-talks/blob/main/scripts/dev-setup.sh).

```bash
RUN_SERVER=true PRETALX_SYNC=false IMPORT_STREAMS=false GEN_FAKE_DATA=true scripts/dev-setup.sh
```

Common toggles:

| Flag                               | Default | Effect                                                                                   |
| ---------------------------------- | ------- | ---------------------------------------------------------------------------------------- |
| `RUN_SERVER=true DJANGO_PORT=8000` | `true`  | Start the dev server when the script finishes                                            |
| `PRETALX_SYNC=true`                | `false` | Import real talks from Pretalx (needs an API token)                                      |
| `IMPORT_STREAMS=true`              | `false` | Import livestream URLs from Google Sheets                                                |
| `GEN_FAKE_DATA=true`               | `true`  | Generate sample events and fake talks                                                    |
| `GEN_TEST_USERS=true`              | `true`  | Create the dev login users                                                               |
| `NO_AVATARS=true`                  | `false` | Skip avatar downloads during Pretalx import                                              |
| `DOWNLOAD_FONT=false`              | `true`  | Skip the Noto font download                                                              |
| `SKIP_STEPS="collectstatic"`       | empty   | Skip individual steps by name (`deps`, `tailwind`, `mailpit`, `django`, `collectstatic`) |

The script creates these dev users (the passwordless login codes land in Mailpit at
<http://localhost:8025>):

- `user1@example.com` / `user2@example.com` - passwordless login
- `mod@example.com` - staff moderator, also passwordless
- `admin@example.com` / `admin` - superuser with password login

!!! tip "Clean slate"

    For a truly fresh start, wipe everything that is not checked in but keep the virtualenv and the
    Pretalx API cache (both are slow to rebuild):

    ```bash
    git clean -fdx -e .venv -e '.pretalx_cache_*'
    scripts/dev-setup.sh
    ```

## Running the dev server

The setup script starts the server for you when `RUN_SERVER=true` (the default). To start it
manually later:

```bash
uv run python manage.py runserver 8000
```

The app is then available at <http://localhost:8000>.

## Tailwind watcher

The setup script downloads a standalone `tailwindcss` binary into `.venv/bin/`. By default it builds
`static/css/tailwind.min.css` once, minified. When you are editing templates and want CSS rebuilt on
every change, run the script with `DEBUG=true` so it starts a watcher instead:

```bash
DEBUG=true scripts/dev-setup.sh
```

Or run the watcher directly:

```bash
.venv/bin/tailwindcss -i ./assets/css/input.css -o ./static/css/tailwind.min.css --watch
```

## Mailpit (local email)

All outgoing email in development goes to [Mailpit](https://mailpit.axllent.org/), which the setup
script downloads and starts automatically. This is where you read the passwordless login codes.

- Web UI: <http://localhost:8025>
- SMTP: `127.0.0.1:1026` (port 1026 instead of the usual 1025, to avoid clashing with apps like
    Proton Mail Bridge that occupy 1025)

Override `MAILPIT_SMTP_PORT` / `MAILPIT_UI_PORT` in the environment if those ports are taken. The
script exports matching `EMAIL_HOST` / `EMAIL_PORT` values so Django talks to the right port without
touching the committed `django-vars.env`.

## Logs

Logging is configured in `event_talks/settings.py`. Files go to the directory named by
`DJANGO_LOGS_DIR` (default: `logs/` in the project root; created automatically):

- `django.log` - everything, as JSON, rotated daily, 30 days kept
- `error.log` - errors only, rotated daily, 90 days kept
- `auth.log` - authentication events, rotated daily, 90 days kept

The console handler prints colored, human-readable output, so during development you usually just
watch the `runserver` terminal.

## Management commands

Custom commands live in `talks/management/commands/` and `users/management/commands/`:

- `import_pretalx_talks` - sync talks, speakers, and rooms from Pretalx
- `import_livestream_urls` - import livestream URLs from Google Sheets
- `update_video_links` - update recorded-video links
- `generate_fake_talks` - generate fake talks for development
- `createuser` - create a passwordless user

All data importers support `--dry-run`. Run any command with `--help` for the full argument list.
See [Management commands](../reference/management-commands.md) for the full reference.

## Where to go next

- [Full stack in Docker](docker-local.md) - production-like testing with Postgres
- [Testing](testing.md) - pytest, coverage, property-based tests
- [Code quality](code-quality.md) - linting, type checking, pre-commit hooks, SonarQube
- [Documentation](documentation.md) - working on this docs site
