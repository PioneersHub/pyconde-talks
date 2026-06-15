---
icon: lucide/rocket
---

# Getting started

This page takes you from a fresh clone to a running development server with sample data and a
working login. Two paths are covered:

- A **Dev Container** (VS Code or GitHub Codespaces) that runs the setup script for you.
- A **local setup** that runs `scripts/dev-setup.sh` directly on your machine.

Both paths end the same way: a Django dev server on port 8000, a Mailpit inbox on port 8025, and
three test users you can log in as.

## Prerequisites

| Tool        | Why it is needed                                          | Notes                                           |
| ----------- | --------------------------------------------------------- | ----------------------------------------------- |
| Git         | Clone the repository.                                     | Any recent version.                             |
| Python 3.14 | Runtime for Django and the management commands.           | Installed and managed by [uv]; see below.       |
| [uv]        | Creates the virtualenv, installs dependencies, runs tools | The setup script installs it if it is missing.  |
| Docker      | Production-like local stack (Postgres + Nginx) only.      | Optional. Not needed for the standard dev flow. |

!!! tip "You do not need to install Python or uv by hand"

    `scripts/dev-setup.sh` installs `uv` if it is not already on your `PATH`, then uses it to create the
    `.venv` and install everything else (including the Tailwind CSS and Mailpit binaries). On a
    supported machine, the only hard prerequisite is Git.

Docker is only required for the production-like full stack described in
[Docker full stack](../development/docker-local.md). The day-to-day workflow on this page uses
SQLite and runs everything on the host.

## Path 1: Dev Container (VS Code or Codespaces)

The repository ships a [Dev Container](https://containers.dev/) definition in
[`.devcontainer/devcontainer.json`](https://github.com/PioneersHub/pyconde-talks/blob/main/.devcontainer/devcontainer.json).
It is based on the `mcr.microsoft.com/devcontainers/python:3.14-bookworm` image and runs the setup
script automatically once the container is created:

```json
"postCreateCommand": "bash scripts/dev-setup.sh"
```

Because `RUN_SERVER` defaults to `true`, the container finishes by starting the dev server. To use
it:

=== "VS Code"

    1. Install the **Dev Containers** extension.
    2. Open the cloned repository folder in VS Code.
    3. When prompted, choose **Reopen in Container** (or run the *Dev Containers: Reopen in Container*
        command).
    4. Wait for `postCreateCommand` to finish. The script runs the full setup and starts the server on
        port 8000 and Mailpit on port 8025.

=== "GitHub Codespaces"

    1. On the GitHub repository page, choose **Code -> Codespaces -> Create codespace**.
    2. Wait for the container to build and `postCreateCommand` to finish.
    3. Forwarded ports for 8000 (the app) and 8025 (Mailpit) appear in the **Ports** panel.

!!! note "Customizing the container run"

    The Dev Container runs the script with all defaults. To change behaviour (for example, to skip fake
    data or pull real Pretalx talks), open a terminal in the container and re-run `scripts/dev-setup.sh`
    with the environment flags from the table below.

## Path 2: Local setup

If you prefer to run on the host directly, clone the repository and run the setup script. The
README's recommended first run keeps things fast and offline:

```bash
RUN_SERVER=true PRETALX_SYNC=false IMPORT_STREAMS=false GEN_FAKE_DATA=true \
  scripts/dev-setup.sh
```

This generates sample data locally and never reaches out to Pretalx or Google Sheets, so it works
without any API tokens.

### What the script does

[`scripts/dev-setup.sh`](https://github.com/PioneersHub/pyconde-talks/blob/main/scripts/dev-setup.sh)
runs these stages in order:

1. **Dependencies.** Install `uv` if missing, create the `.venv`, run
    `uv sync --group dev --group test`, and install the pre-commit hooks with `prek install`. It
    also sets `DJANGO_READ_VARS_FILE` based on whether `django-vars.env` exists (see
    [Configuration](configuration.md)).
2. **Tailwind CSS.** Download the standalone Tailwind binary into `.venv/bin` (or symlink one
    already on your `PATH`) and ensure the `assets/css` and `static/css` folders exist.
3. **Mailpit.** Download the Mailpit binary into `.venv/bin` (or symlink one on your `PATH`).
    Mailpit is the local mail catcher used to read login codes.
4. **Database migrations.** Run `makemigrations` and `migrate`.
5. **Superuser.** Create the admin superuser from `DJANGO_SUPERUSER_EMAIL` /
    `DJANGO_SUPERUSER_PASSWORD` (defaults `admin@example.com` / `admin`).
6. **Test users.** When `GEN_TEST_USERS=true` (the default), create `user1@example.com`,
    `user2@example.com`, and a staff moderator `mod@example.com`.
7. **Noto Sans font.** When `DOWNLOAD_FONT=true`, download `NotoSans.ttf` into `assets/fonts` for
    social card generation.
8. **Sample data.** When `GEN_FAKE_DATA=true`, create the sample events (`pyconde-pydata-2025`,
    `pyconde-pydata-2026`, `pydata-berlin-2026`) and generate fake talks, rooms, and speakers for
    each with `generate_fake_talks`.
9. **Pretalx sync.** When `PRETALX_SYNC=true`, ensure the `DEFAULT_EVENT` exists and run
    `import_pretalx_talks` (requires `PRETALX_API_TOKEN`).
10. **Livestreams.** When `IMPORT_STREAMS=true`, run `import_livestream_urls` from Google Sheets
    (requires the sheet to be configured).
11. **Event assignment.** When `GEN_TEST_USERS=true`, assign each test user to a random active event
    so they have something to see after logging in.
12. **Services.** Build (or watch, in debug mode) the Tailwind CSS, start Mailpit, and, when
    `RUN_SERVER=true`, start the Django dev server.
13. **Static files.** Run `collectstatic` at the end (unless skipped).

!!! info "Mailpit ports in local dev"

    The script starts Mailpit on SMTP port **1026** by default, not the usual 1025. Port 1025 is often
    held by tools like Proton Mail Bridge, which would make Django's login-code emails hang. The script
    exports `EMAIL_HOST` and `EMAIL_PORT` so Django points at the right Mailpit instance, leaving the
    committed `django-vars.env` (where `EMAIL_PORT=1025` is correct for production) untouched. Override
    `MAILPIT_SMTP_PORT` / `MAILPIT_UI_PORT` if those ports are taken. The Mailpit web UI stays on port
    **8025**.

### Setup flags

All flags are environment variables read at the top of the script. Pass them on the command line to
override the defaults:

| Flag                        | Default             | Effect                                                                  |
| --------------------------- | ------------------- | ----------------------------------------------------------------------- |
| `RUN_SERVER`                | `true`              | Start the Django dev server when setup finishes.                        |
| `DJANGO_PORT`               | `8000`              | Port the dev server binds to.                                           |
| `GEN_FAKE_DATA`             | `true`              | Generate sample events, talks, rooms, and speakers.                     |
| `FAKE_DATA_COUNT`           | `50`                | Number of fake talks generated per event.                               |
| `GEN_TEST_USERS`            | `true`              | Create `user1`, `user2`, and the `mod` moderator, and link to events.   |
| `PRETALX_SYNC`              | `false`             | Import real talks from Pretalx (needs `PRETALX_API_TOKEN`).             |
| `IMPORT_STREAMS`            | `false`             | Import livestream URLs from Google Sheets.                              |
| `DOWNLOAD_FONT`             | `true`              | Download Noto Sans for social card rendering.                           |
| `NO_AVATARS`                | `false`             | Skip speaker avatar downloads during a Pretalx sync.                    |
| `IMAGE_FORMAT`              | `webp`              | Image format passed to the Pretalx importer.                            |
| `SKIP_STEPS`                | empty               | Skip stages by name (see below).                                        |
| `DEBUG`                     | `false`             | When `true`, run Tailwind in `--watch` mode instead of a one-off build. |
| `VENV_DIR`                  | `.venv`             | Location of the virtual environment.                                    |
| `DJANGO_SUPERUSER_EMAIL`    | `admin@example.com` | Email for the created superuser.                                        |
| `DJANGO_SUPERUSER_PASSWORD` | `admin`             | Password for the created superuser.                                     |

`SKIP_STEPS` matches stage names as a substring, so you can skip one or several at once. Recognized
names: `deps`, `tailwind`, `mailpit`, `django`, and `collectstatic`. For example:

```bash
# Refresh data without reinstalling dependencies or rebuilding Tailwind.
SKIP_STEPS="deps tailwind" GEN_FAKE_DATA=true scripts/dev-setup.sh
```

!!! tip "Re-running for a clean slate"

    The script is safe to run again. For a fully clean rebuild, remove generated state first while
    keeping the virtualenv and the Pretalx cache:

    ```bash
    git clean -fdx -e .venv -e .pretalx_cache
    ```

### Test users created

| Email               | Login method       | Role                                   |
| ------------------- | ------------------ | -------------------------------------- |
| `user1@example.com` | Email code         | Regular attendee.                      |
| `user2@example.com` | Email code         | Regular attendee.                      |
| `mod@example.com`   | Email code         | Staff moderator (`is_staff=True`).     |
| `admin@example.com` | Password (`admin`) | Superuser with admin and staff access. |

Regular and staff users are passwordless: they sign in with a one-time code emailed to them. Only
the superuser has a password. See [Authentication](authentication.md) for the full picture.

## First login walkthrough

Once setup has finished and the server is running:

1. Open the app at <http://127.0.0.1:8000>.
2. Enter `user1@example.com` (or `user2@example.com`) and submit the login form. If an event picker
    is shown, choose any active event.
3. Open Mailpit at <http://localhost:8025>. You will see the login-code email.
4. Copy the code from the email and paste it into the confirmation form to finish signing in.

To reach the Django admin instead:

1. Open <http://127.0.0.1:8000/admin/>.
2. Sign in with `admin@example.com` and password `admin`.

!!! warning "Login code emails go to Mailpit, not a real inbox"

    In development, all outgoing mail is captured by Mailpit. Codes never leave your machine. Never use
    the insecure default secret key or the `admin`/`admin` credentials in a real deployment.

## What next

- [Configuration](configuration.md) for the full environment variable reference and the
    `django-vars.env` / `docker/.env` sync rule.
- [Authentication](authentication.md) for how passwordless login, Discord OAuth, and admin passwords
    fit together.
- [Development](../development/index.md) for day-to-day commands, iterating with the setup flags,
    and the management commands.

[uv]: https://docs.astral.sh/uv
