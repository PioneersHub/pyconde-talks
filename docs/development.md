# Development

Day-to-day commands and workflows. Claude Code: read this when the user asks about setting up the
project, running the dev server, importing data, or refreshing SonarQube.

## First-time setup

```bash
RUN_SERVER=true PRETALX_SYNC=false IMPORT_STREAMS=false GEN_FAKE_DATA=true scripts/dev-setup.sh
```

Creates dev users:

- `user1@example.com` / `user2@example.com` - passwordless login, codes visible in Mailpit at
  http://localhost:8025
- `admin@example.com` / `admin` - superuser with password login

## Iterating locally

Re-run `scripts/dev-setup.sh` with env flags to refresh state. Add
`git clean -fdx -e .venv -e .pretalx_cache` first for a clean slate.

Flags and defaults are at the top of `scripts/dev-setup.sh`. Common toggles:

- `RUN_SERVER=true DJANGO_PORT=8000` - start the dev server when the script finishes
- `PRETALX_SYNC=true` / `IMPORT_STREAMS=true` - fetch real data (requires API tokens)
- `GEN_FAKE_DATA=true` - generate fake talks and users
- `NO_AVATARS=true` / `DOWNLOAD_FONT=false` - skip slow assets
- `SKIP_STEPS="collectstatic"` - skip individual steps by name

## Management commands

Live in `talks/management/commands/`. All data imports (`import_pretalx_talks`,
`import_livestream_urls`, `update_video_links`, `generate_fake_talks`) support `--dry-run`. Run any
with `--help` for the full argument list.

For the Pretalx importer specifically - flags, the detect-and-review workflow, image regeneration
triggers, scheduling, and the configurable env vars - see [pretalx-sync.md](pretalx-sync.md).

## Full stack locally (Docker + Postgres)

Production-like local testing (Django + Postgres in Docker). Nginx not required since Django can
serve static files directly when `DJANGO_SERVE_STATIC_LOCALLY=true`.

All `docker compose` commands below use `compose.local.yaml` to mount exported static files into the
container. A shell alias helps:

```bash
alias dcl='docker compose -f compose.yaml -f compose.local.yaml'
```

### 1. Prepare local folders

```bash
mkdir -p .local/media .local/logs .local/staticfiles
```

If you get a mount permission error later, fix ownership once:

```bash
sudo chown -R "$USER":"$(id -gn)" .local
chmod -R u+rwX .local
```

### 2. Build image and export static files

```bash
cd docker
rm -rf staticfiles                       # buildx does not clean stale files
docker buildx bake --allow=fs.read=..    # builds linux/amd64 by default
rm -rf ../.local/staticfiles
mv staticfiles ../.local/staticfiles
```

### 3. Start Postgres

```bash
dcl up -d db
```

### 4. (Optional) Restore a database backup

Skip this step if you do not have a backup to restore.

For a `pg_dump` backup (single database):

```bash
# Drop and recreate the target database first
dcl exec -T db sh -lc 'psql -U "$POSTGRES_USER" -d postgres \
  -c "DROP DATABASE IF EXISTS \"$POSTGRES_DB\";"'
dcl exec -T db sh -lc 'psql -U "$POSTGRES_USER" -d postgres \
  -c "CREATE DATABASE \"$POSTGRES_DB\";"'

# Then import the backup
cat /path/to/backup.sql | dcl exec -T db sh -lc 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"'
```

For a `pg_dumpall` backup (full cluster):

```bash
cat /path/to/backup-all.sql | dcl exec -T db sh -lc 'psql -U "$POSTGRES_USER" -d postgres'
```

### 5. Start Django

```bash
dcl up -d
```

### 6. Verify

```bash
dcl ps
dcl logs -f django
curl -fsS "http://127.0.0.1:8000/ht/?format=json"
```

### 7. Running commands in the container

From the `docker/` directory, prefix any management command with `dcl exec django`:

```bash
dcl exec django python manage.py shell
dcl exec django python manage.py shell -v 0 -c "from talks.models import Talk; print(Talk.objects.count())"
dcl exec django python manage.py createsuperuser --email=testing@example.com
dcl exec django python manage.py import_pretalx_talks --event-slug pyconde-pydata-2026
dcl exec django python manage.py dumpdata talks.Rating --indent 2 > ratings.json
```

## Full local CI + SonarQube refresh

The pipeline (sync deps, ruff JSON report, bandit JSON report, pytest, sonar-scanner) lives in the
`/ci` skill (`.claude/skills/ci.md`). Trigger it with `/ci`.
