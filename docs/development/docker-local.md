---
icon: lucide/container
---

# Full stack in Docker

Production-like local testing: the Django image and PostgreSQL run in Docker, just like on the
server. Nginx is not required because Django can serve the exported static files itself when
`DJANGO_SERVE_STATIC_LOCALLY=true`.

All `docker compose` commands below combine `compose.yaml` with `compose.local.yaml`. The local
overlay sets `DJANGO_SERVE_STATIC_LOCALLY=true` and mounts the `.local/` folders (static files,
media, logs) into the container. A shell alias keeps the commands short:

```bash
alias dcl='docker compose -f compose.yaml -f compose.local.yaml'
```

Run all compose commands from the `docker/` directory; that is where `compose.yaml` and the `.env`
file live.

!!! warning "Always use both compose files"

    A bare `docker compose` (without `-f compose.local.yaml`) uses the production paths from
    `compose.yaml`: `/var/log/talks.pycon.de` and `/var/opt/talks.pycon.de/media`. On Docker Desktop or
    Colima those live inside the Linux VM, not on your Mac, so Docker creates them there owned by `root`
    and the container (which runs as UID 65532) cannot write its log files. Django then dies on startup
    with:

    ```
    PermissionError: [Errno 13] Permission denied: '/logs/auth.log'
    ValueError: Unable to configure handler 'auth_file'
    ```

    Changing ownership of the path on the host does not help, because that is not the directory the
    container sees. Use the `dcl` alias everywhere and the logs go to `.local/logs` in the repository
    instead.

## 1. Prepare local folders

From the repository root:

```bash
mkdir -p .local/media .local/logs .local/staticfiles
```

If you get a mount permission error later, fix ownership once:

```bash
sudo chown -R "$USER":"$(id -gn)" .local
chmod -R u+rwX .local
```

## 2. Build the image and export static files

The image must be built with Bake, not `docker compose build`: the bake file defines two targets,
the app image and a `staticfiles-export` target that dumps the collected, content-hashed assets to
`docker/staticfiles`. Building both with the same tag guarantees the `staticfiles.json` manifest
baked into the image matches the exported files.

!!! danger "Bake does not read `.env`, so export the image name first"

    `docker compose` interpolates `${IMAGE_NAME}` and `${IMAGE_TAG}` from `docker/.env`, so it runs
    `talks.pycon.de-django:latest`. **Bake does not read `.env` at all**: its variables come only from
    the real environment, so it falls back to the defaults in `docker/docker-bake.hcl` and tags the
    image `event-talks:latest`.

    Build without exporting anything and the two names never meet. Bake succeeds, `dcl up -d` starts the
    *previous* `talks.pycon.de-django:latest`, and your changes appear to have been ignored, which looks
    exactly like a stale build cache but is not. Check with
    `docker images | grep -E 'event-talks|talks.pycon.de-django'`: two images, two timestamps.

    Bake and compose read the same two variable *names* (`IMAGE_NAME`, `IMAGE_TAG`), so exporting them
    once is enough. Only the defaults differ, and deliberately: bake's `event-talks` is the
    event-agnostic name CI publishes to GHCR, while `.env` carries the per-deployment name.

Export both names from `.env`, then build:

```bash
cd docker

# Bake takes its variables from the environment, not from .env. Same names compose uses.
export IMAGE_NAME="$(grep -E '^IMAGE_NAME=' .env | cut -d= -f2-)"
export IMAGE_TAG="$(grep -E '^IMAGE_TAG=' .env | cut -d= -f2-)"

rm -rf staticfiles                       # buildx does not clean stale files
docker buildx bake --allow=fs.read=..    # builds linux/amd64 by default
rm -rf ../.local/staticfiles
mv staticfiles ../.local/staticfiles
```

Confirm the tag bake will use before a long build, and that it matches what compose will run:

```bash
docker buildx bake --allow=fs.read=.. --print | grep -A2 '"tags"'
dcl config | grep 'image: talks'
```

!!! warning "Do not `source .env` to do this"

    `set -a; source ./.env; set +a` looks like the obvious shortcut, but it aborts partway:
    `DJANGO_SECRET_KEY` contains an unquoted `&`, so the shell stops with `parse error near '&'` and
    every variable *after* that line is silently left unset. The two targeted `export` lines above avoid
    the problem, and are all bake needs.

!!! note "Why move the export?"

    `compose.local.yaml` mounts `../.local/staticfiles` into the container at `DJANGO_STATIC_ROOT`, so
    Django serves exactly the files that were collected during the build.

!!! tip "Rebuilding after a template or static change"

    Templates and the static manifest are baked into the image, so editing a template on the host
    changes nothing until you rebuild and recreate the container:

    ```bash
    docker buildx bake --allow=fs.read=..
    dcl up -d --force-recreate django
    ```

## 3. Start Postgres

```bash
dcl up -d db
```

The database credentials (`POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD`) come from
`docker/.env`. The compose file waits for the Postgres healthcheck before starting Django.

## 4. (Optional) Restore a database backup

Skip this step if you do not have a backup to restore.

=== "pg_dump (single database)"

    ```bash
    # Drop and recreate the target database first
    dcl exec -T db sh -lc 'psql -U "$POSTGRES_USER" -d postgres \
      -c "DROP DATABASE IF EXISTS \"$POSTGRES_DB\";"'
    dcl exec -T db sh -lc 'psql -U "$POSTGRES_USER" -d postgres \
      -c "CREATE DATABASE \"$POSTGRES_DB\";"'

    # Then import the backup
    cat /path/to/backup.sql | dcl exec -T db sh -lc 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"'
    ```

=== "pg_dumpall (full cluster)"

    ```bash
    cat /path/to/backup-all.sql | dcl exec -T db sh -lc 'psql -U "$POSTGRES_USER" -d postgres'
    ```

## 5. Start Django

```bash
dcl up -d
```

The container entrypoint runs migrations on startup. Django listens on `127.0.0.1:8000` (the port
mapping is bound to localhost only).

## 6. Verify

```bash
dcl ps
dcl logs -f django
curl -fsS "http://127.0.0.1:8000/ht/?format=json"
```

The `/ht/` endpoint is the django-health-check status page; the container's own healthcheck polls
the same URL, so `dcl ps` should show the `django` service as `healthy` after about a minute
(`start_period` covers the entrypoint migrations).

## 7. Running commands in the container

From the `docker/` directory, prefix any management command with `dcl exec django`:

```bash
dcl exec django python manage.py shell
dcl exec django python manage.py shell -v 0 -c "from talks.models import Talk; print(Talk.objects.count())"
dcl exec django python manage.py createsuperuser --email=testing@example.com
dcl exec django python manage.py import_pretalx_talks --event-slug pyconde-pydata-2026
dcl exec django python manage.py dumpdata talks.Rating --indent 2 > ratings.json
```

!!! warning "The container filesystem is read-only"

    The Django container runs with a read-only root filesystem, all Linux capabilities dropped, and
    `no-new-privileges`. The app can only write to the mounted media and logs volumes and to a `/tmp`
    tmpfs. If a command needs to write elsewhere, that is a sign it should not run in production either.

## Full rebuild, in one block

The same steps as above, for when you want to paste rather than read. Restoring the backup is the
only optional part.

```bash
cd /path/to/pyconde-talks
mkdir -p .local/media .local/logs .local/staticfiles

cd docker
alias dcl='docker compose -f compose.yaml -f compose.local.yaml'

# Bake reads the environment, not .env (see step 2). Without these two exports the build is
# tagged event-talks:latest and compose keeps running the previous image.
export IMAGE_NAME="$(grep -E '^IMAGE_NAME=' .env | cut -d= -f2-)"
export IMAGE_TAG="$(grep -E '^IMAGE_TAG=' .env | cut -d= -f2-)"

dcl down
rm -rf staticfiles
docker buildx bake --allow=fs.read=..
rm -rf ../.local/staticfiles
mv staticfiles ../.local/staticfiles

dcl up -d db

# Optional: start from a backup instead of an empty database.
dcl exec -T db sh -lc 'psql -U "$POSTGRES_USER" -d postgres \
  -c "DROP DATABASE IF EXISTS \"$POSTGRES_DB\";"'
dcl exec -T db sh -lc 'psql -U "$POSTGRES_USER" -d postgres \
  -c "CREATE DATABASE \"$POSTGRES_DB\";"'
cat /path/to/backup.sql | dcl exec -T db sh -lc 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"'

dcl up -d
dcl ps
curl -fsS "http://127.0.0.1:8000/ht/?format=json"
dcl logs -f django
```

A healthy stack answers `/ht/` with every check `OK`, including `Cache` (Redis) and `Database`:

```json
{"Cache(alias='default')": "OK", "Database(alias='default')": "OK", "Storage(alias='default')": "OK"}
```
