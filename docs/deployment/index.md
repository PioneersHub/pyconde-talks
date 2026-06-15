---
icon: lucide/server
---

# Production deployment

Everything needed to run the site in production lives in the
[`docker/`](https://github.com/PioneersHub/pyconde-talks/blob/main/docker) directory: a multi-stage
Dockerfile, a Buildx Bake definition, a Compose file for the app and its PostgreSQL database, and
the supporting shell scripts. Nginx terminates TLS in front of the container and serves static and
media files directly from disk.

## The docker/ directory

| File                                                                                                             | Purpose                                                                         |
| ---------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------- |
| [`Dockerfile`](https://github.com/PioneersHub/pyconde-talks/blob/main/docker/Dockerfile)                         | Multi-stage build: builder, static-files export, hardened runtime               |
| [`docker-bake.hcl`](https://github.com/PioneersHub/pyconde-talks/blob/main/docker/docker-bake.hcl)               | Buildx Bake definition for the `django` and `staticfiles-export` targets        |
| [`compose.yaml`](https://github.com/PioneersHub/pyconde-talks/blob/main/docker/compose.yaml)                     | Production Compose file: PostgreSQL 18 + the Django app                         |
| [`compose.local.yaml`](https://github.com/PioneersHub/pyconde-talks/blob/main/docker/compose.local.yaml)         | Local override: serve static files from Django, mount `.local/` dirs            |
| [`docker-entrypoint.sh`](https://github.com/PioneersHub/pyconde-talks/blob/main/docker/docker-entrypoint.sh)     | Container entrypoint: run migrations (with retry), then start Daphne            |
| [`ensure_permissions.sh`](https://github.com/PioneersHub/pyconde-talks/blob/main/docker/ensure_permissions.sh)   | Host-side script that sets ownership of the media, log, and static dirs         |
| [`backup_db.sh`](https://github.com/PioneersHub/pyconde-talks/blob/main/docker/backup_db.sh)                     | Timestamped `pg_dump` of the running database (see [Operations](operations.md)) |
| [`deploy/deploy-event.sh`](https://github.com/PioneersHub/pyconde-talks/blob/main/docker/deploy/deploy-event.sh) | Server-side forced-command deploy script (see [CI/CD](ci-cd.md))                |
| `.env`                                                                                                           | Compose environment file (runtime configuration, see below)                     |

## Build targets

One Bake build produces two artifacts. Building them together is deliberate: the `staticfiles.json`
manifest baked into the app image is guaranteed to match the exported assets, so content-hashed
`{% static %}` URLs can never drift out of sync with what nginx serves.

### django (the runtime app)

A multi-stage image built on Chainguard's hardened, minimal
[`wolfi-base`](https://images.chainguard.dev/directory/image/wolfi-base/overview), pinned by digest
(Renovate keeps the digest fresh so daily CVE rebuilds still land).

- **Builder stage:** `uv` (copied as a static binary from Astral's distroless image) installs the
    locked production dependencies against Wolfi's apk-managed Python 3.14, the standalone Tailwind
    CSS binary compiles the stylesheet, and `collectstatic` runs with `ManifestStaticFilesStorage`
    so every asset gets a content-hashed name.
- **Runtime stage:** only `python-3.14`, `tzdata`, `libstdc++`, and `libffi` are added on top of the
    base. No shell tooling (no `curl`, no `vim`, no compiler). The virtualenv and app code are
    copied in root-owned and read-only.
- **Non-root:** the container runs as wolfi-base's predefined `nonroot` account (UID/GID **65532**).
    No user is created at build time.
- **Daphne:** the default command is `daphne -b 0.0.0.0 -p 8000 event_talks.asgi:application`. The
    entrypoint applies database migrations first (three attempts, then it refuses to start rather
    than serve a half-migrated schema).
- **Pure-Python health check:** since the image has no `curl` or `wget`, the `HEALTHCHECK` probes
    `http://127.0.0.1:8000/ht/?format=json` with `urllib` and asserts HTTP 200, so a redirect or a
    degraded response is never read as healthy.

`compose.yaml` adds runtime hardening on top: a read-only root filesystem, all Linux capabilities
dropped, `no-new-privileges`, and a small `tmpfs` on `/tmp` (the only writable paths are the mounted
media and log volumes and `/tmp`). Daphne is published on `127.0.0.1:8000` only; nginx is the public
entry point.

### staticfiles (the asset export)

A `scratch`-based stage containing nothing but the collected, content-hashed static files. Locally,
Bake exports it to `docker/staticfiles/` on disk. In CI it is pushed to GHCR as its own image
(`event-talks-static`) that the server extracts during deploy.

## Manual build and run

The automated pipeline in [CI/CD](ci-cd.md) is the normal path. The manual flow below is useful for
a first-time server setup or an offline build.

### Build

```bash
cd docker
docker buildx bake --allow=fs.read=..
```

This builds both targets: the `django` image is loaded into the local daemon and the static files
land in `docker/staticfiles/`.

### Run

```bash
# Prepare directories
sudo mkdir -p ${MEDIA_DIR} ${STATIC_DIR} ${LOGS_DIR}

# Copy static files
mv docker/staticfiles/* ${STATIC_DIR}/

# Set permissions for Nginx (www-data) and Django (UID 65532)
sudo APP_DOMAIN=talks.example.com ./docker/ensure_permissions.sh

# Start PostgreSQL and Django
cd docker
docker compose up -d
```

!!! warning "UID migration: 10000 to 65532"

    The container's Django user changed from UID **10000** to **65532** (Chainguard's nonroot
    convention). On a server that was set up before this change, the mounted media and log volumes are
    still owned by 10000, so the new container cannot write to them. Re-run `ensure_permissions.sh` (its
    defaults are now 65532) **before or with** the deploy to re-own them:
    `sudo APP_DOMAIN=<target> ./docker/ensure_permissions.sh`. The UID is set in `docker/Dockerfile` and
    mirrored in `ensure_permissions.sh`; there is no security difference between 10000 and 65532, so
    re-owning to 65532 is simplest.

## ensure_permissions.sh

The app container and nginx need different access to the same host directories.
[`ensure_permissions.sh`](https://github.com/PioneersHub/pyconde-talks/blob/main/docker/ensure_permissions.sh)
(run as root on the host) sets that up in one shot:

| Directory    | Default path                            | Owner                       | Access                                                 |
| ------------ | --------------------------------------- | --------------------------- | ------------------------------------------------------ |
| Static files | `/var/cache/${APP_DOMAIN}/staticfiles/` | nginx                       | nginx read-only (Django never touches them at runtime) |
| Media        | `/var/opt/${APP_DOMAIN}/media/`         | Django (65532), nginx group | Django read/write, nginx read                          |
| Logs         | `/var/log/${APP_DOMAIN}/`               | Django (65532)              | Django read/write only                                 |

Everything is overridable via environment variables: `APP_DOMAIN`, `MEDIA_DIR`, `LOGS_DIR`,
`STATIC_DIR`, `NGINX_UID`/`NGINX_GID` (auto-detected from the `nginx` or `www-data` user, falling
back to 33), and `DJANGO_UID`/`DJANGO_GID` (default 65532). When `DEFAULT_EVENT` is set, the script
also pre-creates the event's `talk_images/` subdirectory under media.

## Environment files

`docker/.env` is the Compose environment file. It configures both the containers themselves
(`CONTAINER_PREFIX`, `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, `IMAGE_NAME`, `IMAGE_TAG`,
the `MOUNT_MEDIA_ROOT`/`MOUNT_LOGS_DIR` host paths) and the Django app inside, since `compose.yaml`
passes the whole file to the container via `env_file`.

Two variables matter at **build** time as well: `DJANGO_STATIC_ROOT` and
`DJANGO_STATICFILES_STORAGE` are forwarded as build args, and the storage backend used during
`collectstatic` must match the runtime backend. If the build used the plain storage but the app runs
with `ManifestStaticFilesStorage`, every page would 500 with "Missing staticfiles manifest entry".

!!! note "Keep the two env files in sync"

    `django-vars.env` (local development) and `docker/.env` (Docker Compose) document the same
    variables. When you add, rename, or remove a variable in one, update the other in the same commit.
    See [django-vars.env](https://github.com/PioneersHub/pyconde-talks/blob/main/django-vars.env) for
    the full annotated reference.

Each deployment target keeps its own `.env` on its own server; the image itself is event-agnostic.
Nothing site-specific is baked in at build time.

## Where to go next

- [CI/CD pipeline](ci-cd.md) - the automated, tag-driven build and deploy via GitHub Actions and
    GHCR, including the one-time server setup and rollback.
- [Nginx](nginx.md) - the reverse proxy in front of Daphne: TLS, security headers, rate limiting,
    and static file serving.
- [Operations](operations.md) - day-two concerns: backups, health checks, logs, and the Pretalx
    detect cron.
