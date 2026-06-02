# Pretalx sync

How the project imports talks from Pretalx, when it touches the live data, and how admins review
changes before they go live. The implementation lives under
[talks/management/commands/\_pretalx/](../talks/management/commands/_pretalx/); this file is the
operator's manual.

## TL;DR

- `manage.py import_pretalx_talks` is the workhorse. It is idempotent: an unchanged Pretalx event is
  a no-op on the second run (no DB writes, no fresh social cards, just a "unchanged" count in the
  report).
- `--detect-only` lets the importer run periodically and record diffs without applying them. Admins
  triage them at `/admin/talks/pendingpretalxchange/` and click **Apply selected** when ready.
- The "Check Pretalx now" object-tool button in that admin page re-runs the detect-only importer for
  `DEFAULT_EVENT` so admins do not need shell access.
- A plain-text email digest goes to `PRETALX_DIGEST_RECIPIENTS` (or `ADMINS`) at the end of any
  detect run that produced or refreshed at least one pending row.

## Modes

`import_pretalx_talks` runs in one of these modes depending on the flags you pass:

| Flag             | What changes happens to the DB                                    |
| ---------------- | ----------------------------------------------------------------- |
| _(default)_      | Full mutation: create/update/delete Talks, Speakers, Rooms        |
| `--dry-run`      | Nothing - just logs what would happen                             |
| `--detect-only`  | Writes `PendingPretalxChange` rows only; live data untouched      |
| `--no-update`    | Skips updates to existing rows; still creates new ones            |
| `--skip-images`  | Skips talk-image (social-card) generation                         |
| `--force-images` | Regenerates every existing talk's image (see "Image regen" below) |

`--detect-only` and the mutation modes are mutually exclusive in spirit (the former records intent,
the latter applies it). `--skip-images` wins when combined with `--force-images`.

Other flags (`--event-slug`, `--event-name`, `--pretalx-event-url`, `--api-token`, `--max-retries`,
`--no-avatars`, `--image-format`) are described in `--help` and have not changed.

## Change detection

The importer compares each Pretalx submission against the local `Talk` it maps to:

- If every field already matches, the row is reported as **unchanged**. Nothing is written,
  `updated_at` does not bump, and the social card is not regenerated.
- If at least one field or speaker association differs, the row is reported as **updated**. Only the
  fields that actually differ are written (`save(update_fields=...)`), so manual local edits to
  other fields survive.

The end-of-run report breaks down counts:
`... created, ... updated, ... unchanged, ... detected, ... deleted, ... skipped, ... failed, ... total`.

## Rooms (event-scoped, matched by Pretalx id)

Rooms belong to an event: the same physical room reused across events is a separate `Room`
row per event (`Room.event`, `on_delete=PROTECT`). Room names are unique **per event**, not
globally.

The importer matches a submission's room by the stable Pretalx room id
(`slot.room.id`, stored on `Room.pretalx_id`), falling back to `(event, name)` for legacy
rows that predate id-keying. This is what makes renames safe:

- **Renamed on Pretalx** (same id, new name): the existing `Room` is **renamed in place**, so
  its streamings, `slido_link`, `capacity`, and all `Talk` FKs stay attached. No orphan row is
  left behind. (Before id-keying, a rename created a brand-new room and stranded the old one's
  streamings and config.)
- **Legacy room matched by name**: its `pretalx_id` is **stamped** on the first sync, converting
  it to an id-keyed row so future renames are handled in place.
- **New room**: created under the event.
- A name match whose stored id differs from the incoming id is logged and left alone (two
  distinct Pretalx rooms never overwrite each other's id).

In `--detect-only` mode the Room table is never written: a pending rename is recorded as a
reviewable `room` field diff (carrying `new_pretalx_id`) and the actual rename happens only when
the change is applied. A **pure rename** (the room a talk already sits in was renamed, with no
other change) is detected explicitly so it still shows up for review instead of silently
applying only on a direct sync.

`Room.event` is **required** (NOT NULL): every room belongs to exactly one event. The importer
always sets it, and `generate_fake_talks` uses a synthetic `fake-event` when no `--event-slug` is
given rather than producing event-less rooms. `PROTECT` blocks deleting an event that still has
rooms.

### Migrating an existing database

The change ships as four migrations that must run in order (the column is nullable only during
the backfill window):

1. `0024_room_event_pretalx_id` - additive, adds nullable `event` + `pretalx_id` (zero-downtime).
2. `0025_backfill_room_event` - assigns each existing room its event from its talks (a room with
   no talks falls back to the newest event; a room whose talks span multiple events aborts the
   migration loudly rather than guessing - rooms are expected to be per-event).
3. `0026_room_event_scoped_constraints` - drops the global unique on `name` and adds the
   per-event `(event, name)` and partial `(event, pretalx_id)` constraints.
4. `0027_room_event_required` - tightens `event` to NOT NULL once the backfill has populated
   every row.

`pretalx_id` for existing rooms is **not** backfilled by a migration (there is nothing local to
map from); it is stamped lazily on the next real sync via the `(event, name)` fallback. On a
production database, snapshot the DB before applying migration `0025`, and verify no
`Room.event IS NULL` rows remain before `0027` runs.

## Image (social card) regeneration

For an existing talk, the social-card image is regenerated when **any** of these is true (and
`--skip-images` is not set):

1. The talk's data or speaker set changed (the normal "updated" path).
2. `--force-images` was passed.
3. A still-attached speaker's name or avatar changed earlier in the same run. The bulk-upsert step
   returns the set of changed speaker IDs and the per-talk loop checks for overlap.
4. The current image file is missing, _or_ a social-card template PNG for the event was touched
   after the image's mtime. Useful when you swap a template on disk: the next import re-renders
   affected cards automatically.

New talks always get an image (unless `--skip-images` is set).

## Detect-and-review workflow

`--detect-only` is the safe periodic-check mode. The importer runs the same diff logic but writes
the result to `PendingPretalxChange` rows instead of mutating the live `Talk` / `Speaker` / `Room`
graph.

### Pending change model

Each row carries:

- `event`, `pretalx_code`, `kind` (`create` / `update` / `delete`).
- `talk` FK (`null` for `create`, set to `null` on `delete`).
- `field_diffs` - `{field: {"old": ..., "new": ...}}` for update.
- `speaker_diffs` - `{"added": [...], "removed": [...]}`.
- `pretalx_payload` - a snapshot rich enough to apply later without going back to Pretalx.
- `first_detected_at`, `last_detected_at`, `applied_at`, `applied_by`, `dismissed_at`,
  `dismissed_by`.

A partial-unique constraint enforces at most one **open** (neither applied nor dismissed) row per
`(event, pretalx_code)`. Re-running detect for an unchanged diff is idempotent: it refreshes the
same row instead of creating a duplicate. After a row is applied/dismissed, a fresh detection opens
a new row.

### Admin UI

`/admin/talks/pendingpretalxchange/` shows the queue with status, kind, event filters, and a
one-line summary per row. Actions:

- **Apply selected pending Pretalx changes** - runs `apply_change` for each pending row in the
  selection. Updates only touch fields recorded in `field_diffs`, so manual local edits to other
  fields are preserved. Wrapped in a transaction.
- **Dismiss selected pending Pretalx changes** - flips `dismissed_at`.
- **Check Pretalx now** (object-tool button at the top of the list) - synchronously re-runs
  `import_pretalx_talks --detect-only` for `settings.DEFAULT_EVENT`. The request takes 10-30 s for a
  500-talk event; the browser is intentionally blocked because there is no worker process to hand
  the job off to. Once it returns, the page reloads and any new/refreshed rows are visible at the
  top of the list.

Closed (applied or dismissed) rows stay in the table as an audit trail and are filtered by the
**status** sidebar.

### Email digest

At the end of any `--detect-only` run that produced or refreshed at least one pending row, the
importer sends a single plain-text email to the configured recipients. Body contains the per-row
summary (`.summarize()`) and an admin URL.

`PRETALX_DIGEST_RECIPIENTS` controls who gets it:

- **unset / empty value** - fall back to Django's `ADMINS`. This is what ships out of the box.
- **comma-separated list of addresses** - send to exactly those.
- **`-` (single dash)** - disable the digest entirely. Useful when you want `ADMINS` configured but
  do not want this particular email.

The send uses `django.core.mail.send_mail`, which routes through whatever email backend is
configured (django-anymail/Mailgun in production, the console backend in dev).

## Scheduling periodic checks

Cron / systemd-timer is the recommended driver. Detect-only is short and synchronous; it never holds
a long-running connection open.

Sample crontab entry (every 10 minutes):

```cron
*/10 * * * * cd /srv/pyconde-talks && /srv/pyconde-talks/.venv/bin/python manage.py \
    import_pretalx_talks --detect-only --event-slug=pyconde-pydata-2026 >> logs/detect.log 2>&1
```

Tips:

- Use the venv's `python`, not the system `python`, so the cron job picks up the project's deps
  without needing `source`.
- Redirect stdout/stderr to a log file under `logs/` so failed runs are greppable.
- Combine with logrotate as you would for any other Django log.

`/etc/systemd/system/pretalx-detect.timer` is the equivalent if you prefer systemd. The unit file
just calls the same `manage.py` invocation.

If you ever decide to graduate from manual review to auto-apply, add a second cron entry that runs
the importer without `--detect-only` on a slower cadence. The two cron entries can coexist: the
detect job populates the audit table; the apply job is the source of truth for the live data.

## Configuration reference

Settings the sync feature reads (also in [django-vars.env](../django-vars.env)):

- `PRETALX_API_TOKEN` - API token for the Pretalx instance. Required.
- `DEFAULT_EVENT` - event slug used by the admin "Check Pretalx now" button and as the fallback when
  `--event-slug` is omitted on the CLI.
- `PRETALX_DIGEST_RECIPIENTS` - see above. Defaults to fall-back-to-ADMINS.
- `ADMINS` / `ADMIN_EMAILS` - Django's classic admin list; reused as the digest fallback recipient
  list.

## Architecture pointers

The importer is split into small modules under
[talks/management/commands/\_pretalx/](../talks/management/commands/_pretalx/):

| Module          | Responsibility                                                   |
| --------------- | ---------------------------------------------------------------- |
| `client.py`     | Pretalx API setup + retry logic                                  |
| `context.py`    | `ImportContext` - the frozen dataclass passed everywhere         |
| `mixins.py`     | The `Command` plumbing: per-submission loop, mode banners        |
| `submission.py` | Flatten / normalize a Pretalx `Submission` into `SubmissionData` |
| `rooms.py`      | Room get-or-create + bulk upsert                                 |
| `speakers.py`   | Speaker get-or-create + bulk upsert (returns visual-change set)  |
| `talks.py`      | `create_talk`, `update_talk` + change detection (`_diff_*`)      |
| `images.py`     | Social-card generation, template-mtime helpers                   |
| `avatars.py`    | Avatar prefetch / disk cache                                     |
| `pending.py`    | Detect-only: build diff, upsert `PendingPretalxChange` rows      |
| `apply.py`      | Apply a pending row back onto a live `Talk`                      |
| `digest.py`     | Build and send the summary email                                 |

The admin glue is in [talks/admin_pretalx.py](../talks/admin_pretalx.py) (bulk actions, "Check
Pretalx now" view) and the change-list template at
[templates/admin/talks/pendingpretalxchange/change_list.html](../templates/admin/talks/pendingpretalxchange/change_list.html).
