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

```bash
cd docker
rm -rf staticfiles                       # buildx does not clean stale files
docker buildx bake --allow=fs.read=..    # builds linux/amd64 by default
rm -rf ../.local/staticfiles
mv staticfiles ../.local/staticfiles
```

!!! note "Why move the export?"

    `compose.local.yaml` mounts `../.local/staticfiles` into the container at `DJANGO_STATIC_ROOT`, so
    Django serves exactly the files that were collected during the build.

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
