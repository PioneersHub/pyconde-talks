---
icon: lucide/activity
---

# Operations

Day-two tasks for a running production deployment: backups, health checks, logs, the Pretalx detect
cron, and rollback. For the build and the one-time server setup, see
[Production deployment](index.md) and [CI/CD](ci-cd.md).

## Database backups

[`docker/backup_db.sh`](https://github.com/PioneersHub/pyconde-talks/blob/main/docker/backup_db.sh)
dumps the PostgreSQL database to a timestamped `.sql` file. It runs `pg_dump` inside the running
Postgres container and writes one plain-SQL dump per run:

```bash
POSTGRES_CONTAINER=talks.pycon.de-postgres BKP_DIR=/var/backups/talks ./docker/backup_db.sh
```

Both variables are required, and the script aborts with a clear error if either is unset:

- `POSTGRES_CONTAINER` - the name of the running Postgres container (the
    `${CONTAINER_PREFIX}-postgres` defined in `compose.yaml`).
- `BKP_DIR` - the directory where backups are written. The file lands at
    `${BKP_DIR}/<YYYY-MM-DD_HH-MM-SS>-postgres-backup.sql`.

`POSTGRES_USER` and `POSTGRES_DB` are resolved inside the container, so they come from that
container's environment, not your shell.

!!! note "It fails loudly instead of leaving a fake backup"

    A naive `pg_dump > file` truncates the output file before `pg_dump` runs, so a failed dump (DB down,
    wrong container, auth error) would leave a 0-byte file and still exit 0. This script dumps to a
    `.partial` temp file and only promotes it to the final name once `pg_dump` actually succeeds. A
    failed dump leaves no file and exits non-zero, so a cron wrapper can detect it.

Schedule it from cron the same way as the Pretalx detect job below, and rotate or off-site the dump
directory according to your retention policy.

### Restoring

To restore a dump, drop and recreate the target database, then pipe the SQL file back in through the
container. The full commands (for both single-database `pg_dump` files and full-cluster `pg_dumpall`
files) are documented in the local Docker guide, which uses the same compose stack: see
[Full stack in Docker](../development/docker-local.md#4-optional-restore-a-database-backup).

## Health checks

The app exposes an unauthenticated health endpoint at `/ht/`, backed by `django-health-check`. Add
`?format=json` for a machine-readable response. It runs a deliberately cheap, self-contained set of
checks:

- Cache
- Database
- Storage
- Disk (psutil)
- Memory (psutil)

The Mail check is deliberately **excluded**: it would open a real SMTP/Mailgun connection on every
hit, which would let anyone drive outbound mail-backend connections and would flip the container to
"unhealthy" during an unrelated email-provider outage, triggering false deploy rollbacks. Monitor
mail deliverability separately.

```bash
curl -fsS "https://talks.pycon.de/ht/?format=json"
```

The Docker container has its own `HEALTHCHECK` that probes `/ht/?format=json` with the bundled
Python (the hardened image has no `curl`/`wget`) and asserts HTTP 200, so a redirect or degraded
response is never read as healthy. The compose healthcheck uses a short interval with a
`start_period` long enough to cover the entrypoint migrations, so the container flips to "healthy"
within roughly 15 seconds of the app answering. The deploy script keys its health gate off this same
container status, which is why a deploy confirms (or rolls back) quickly instead of waiting out a
long interval.

## Logs

Logging is structured (`structlog` + `django-structlog`). The console gets a colored, human-readable
renderer; the files get JSON, one event per line. Files are written under `DJANGO_LOGS_DIR` (the
mounted logs volume) and rotated daily by a `TimedRotatingFileHandler` at midnight:

| File         | Contents                                              | Kept           |
| ------------ | ----------------------------------------------------- | -------------- |
| `django.log` | Root and `django` logs, plus app `INFO`+ events       | 30 daily files |
| `error.log`  | `ERROR`+ from requests, security, DB, and app loggers | 90 daily files |
| `auth.log`   | The `auth` logger (`INFO`+)                           | 90 daily files |

!!! info "Emails are hashed in logs"

    When `LOG_EMAIL_HASH` is true (the default), email addresses are hashed before they reach the logs,
    so the JSON files carry no raw PII. Keep this on in production.

Because the files rotate by date, the standard `logrotate` setup is not strictly required, but you
may still want it to compress or off-site old files. The container's stdout/stderr (the console
renderer) is captured by Docker's `json-file` driver, capped at `10m` per file with 3 files retained
(set in `compose.yaml`).

## Scheduling the Pretalx detect cron

The Pretalx importer has a `--detect-only` mode that records pending changes for review without
touching live data. Running it on a schedule keeps the admin's "pending changes" view fresh. A
typical crontab entry runs every 10 minutes:

```cron
*/10 * * * * cd /srv/pyconde-talks && /srv/pyconde-talks/.venv/bin/python manage.py \
    import_pretalx_talks --detect-only --event-slug=pyconde-pydata-2026 >> logs/detect.log 2>&1
```

Use the venv's `python` (not the system one) so the job picks up the project's dependencies, and
redirect output to a log file so failed runs are greppable. A systemd timer works equally well. The
detect-and-review workflow, the full flag reference, and the auto-apply variant are documented in
[Pretalx sync](../reference/pretalx-sync.md#scheduling-periodic-checks).

## Rollback

A failed health check rolls back automatically: `deploy-event` re-points the target's `.env` at the
previous git sha and re-verifies that it is healthy. To roll back a healthy-but-bad deploy, push a
new tag on the previous good commit. The full rollback procedure (including the direct-SSH
forced-command path) is in [CI/CD](ci-cd.md#rollback).

## Re-running ensure_permissions.sh after UID changes

The container's Django user is UID **65532**. If a server was set up before the move from UID 10000,
or if you ever change the mounted volume ownership, re-run the permissions script as root so the
container can write media and logs again:

```bash
sudo APP_DOMAIN=<target> ./docker/ensure_permissions.sh
```

This re-owns the media and log directories to 65532 and resets the static directory to nginx
read-only. See the [UID migration warning](index.md#manual-build-and-run) and the
[`ensure_permissions.sh` section](index.md#ensure_permissionssh) on the deployment overview for the
full explanation of which directory gets which owner and mode.
