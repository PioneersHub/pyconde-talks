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

## Periodic Pretalx checks (detect-and-review)

`import_pretalx_talks --detect-only` runs the same diff logic as a normal import but records every
diff as a `PendingPretalxChange` row instead of mutating `Talk`/`Speaker`/`Room`. Admins triage the
queue in `/admin/talks/pendingpretalxchange/`:

- **Apply selected** writes the change to the live DB. Only fields recorded in `field_diffs` are
  touched, so manual local edits to other fields survive.
- **Dismiss selected** marks the row as ignored.
- **Check Pretalx now** (object-tool button) re-runs the detect synchronously for
  `settings.DEFAULT_EVENT`. Typically 10-30s for a 500-talk event.

When detect-only finishes and any pending rows were created or refreshed, a plain-text digest goes
out to `settings.PRETALX_DIGEST_RECIPIENTS` (or `ADMINS` as a fallback). Set the recipient list to
`[]` to disable.

Periodic checks are driven by cron / systemd timer. Sample crontab entry (every 10 minutes):

```cron
*/10 * * * * cd /srv/pyconde-talks && /srv/pyconde-talks/.venv/bin/python manage.py \
    import_pretalx_talks --detect-only --event-slug=pyconde-pydata-2026 >> logs/detect.log 2>&1
```

Apply mode (the normal, mutating import) keeps its own cron entry for the auto-apply flow if/when
you decide to graduate from manual review.

## Full local CI + SonarQube refresh

The pipeline (sync deps, ruff JSON report, bandit JSON report, pytest, sonar-scanner) lives in the
`/ci` skill (`.claude/skills/ci.md`). Trigger it with `/ci`.
