---
icon: lucide/terminal
---

# Management commands

This project ships four custom Django management commands. They cover the full content pipeline:
pulling talks and speakers from Pretalx, attaching livestream URLs from a Google Sheet, backfilling
recorded-video links from Vimeo, and seeding a development database with realistic fake data.

Run any command through `uv` in development:

```bash
uv run python manage.py <command> [flags]
```

In a deployed container the project is on the path, so the `uv run` prefix is not needed:

```bash
python manage.py <command> [flags]
```

Every command that imports from an external source (`import_pretalx_talks`,
`import_livestream_urls`, `update_video_links`) supports `--dry-run`, which fetches and reports but
never commits. Use it to preview a run before letting it touch live data. The exception is
`generate_fake_talks`, which has no `--dry-run` flag because it only seeds a local development
database.

| Command                                             | Source                     | Writes to                          |
| --------------------------------------------------- | -------------------------- | ---------------------------------- |
| [`import_pretalx_talks`](#import_pretalx_talks)     | Pretalx REST API           | Talks, Speakers, Rooms, Events     |
| [`import_livestream_urls`](#import_livestream_urls) | Google Sheet (xlsx export) | Streamings                         |
| [`update_video_links`](#update_video_links)         | Vimeo API                  | Talk video links                   |
| [`generate_fake_talks`](#generate_fake_talks)       | Faker (local generator)    | Talks, Speakers, Rooms, Streamings |

!!! info "Where the env defaults come from"

    Most flags default to a Django setting (for example `--api-token` defaults to `PRETALX_API_TOKEN`).
    Those settings are populated from environment variables, which in development live in
    [`django-vars.env`](https://github.com/PioneersHub/pyconde-talks/blob/main/django-vars.env). Passing
    a flag on the command line always overrides the setting.

## `import_pretalx_talks`

Imports talks and speakers from the Pretalx REST API into the local database. This is the workhorse
command and the one you run most often. It is idempotent: a second run against an unchanged Pretalx
event makes no database writes and regenerates no social cards.

This page documents the flags and a few quick recipes. For the operator deep dive (sync modes,
change detection, event-scoped rooms, image regeneration, the detect-and-review workflow, the email
digest, and scheduling), see [Pretalx sync](pretalx-sync.md).

### Purpose

- Create new `Talk` and `Speaker` rows for Pretalx submissions not yet in the database.
- Update existing rows when a field or speaker association changed, writing only the fields that
    actually differ so manual local edits to other fields survive.
- Delete talks that disappeared from the Pretalx event.
- Generate (or refresh) the social-card image for each talk.
- Optionally run in detect-only mode that records reviewable diffs instead of mutating live data.

### Usage

```bash
uv run python manage.py import_pretalx_talks \
  --pretalx-event-url https://pretalx.com/my-event/ \
  --event-slug my-event \
  --api-token TOKEN
```

When `--event-slug` is omitted it falls back to the `DEFAULT_EVENT` setting (`pyconde-pydata-2026`).
When `--api-token` is omitted it uses `PRETALX_API_TOKEN`. The event URL falls back to the
`Event.pretalx_url` field, then to `https://pretalx.com/<event-slug>`.

### Flags

| Flag                             | Default                          | What it does                                                                                           |
| -------------------------------- | -------------------------------- | ------------------------------------------------------------------------------------------------------ |
| `--pretalx-event-url`            | `""`                             | Base event URL on Pretalx, used to build talk links. Falls back to the `Event.pretalx_url` field.      |
| `--event-slug`                   | `DEFAULT_EVENT` setting          | Event slug in the Django app. Not necessarily the same as the Pretalx slug.                            |
| `--event-name`                   | `""`                             | Human-readable name used when creating a new `Event`. When set, skips fetching the name from Pretalx.  |
| `--api-token`                    | `PRETALX_API_TOKEN` setting      | API token for the Pretalx API.                                                                         |
| `--no-update`                    | off                              | Skip updating existing talks and speakers. New rows are still created.                                 |
| `--dry-run`                      | off                              | Simulate the import without saving to the database.                                                    |
| `--detect-only`                  | off                              | Record diffs as `PendingPretalxChange` rows for admin review without mutating live data.               |
| `--max-retries`                  | `3`                              | Maximum number of retries for API requests.                                                            |
| `--skip-images`                  | off                              | Skip generating or updating talk social cards. Wins when combined with `--force-images`.               |
| `--force-images`                 | off                              | Regenerate every existing talk's social card, even when nothing changed. Ignored with `--skip-images`. |
| `--no-avatars`                   | off                              | Skip downloading and pasting speaker avatars onto social cards.                                        |
| `--use-cache` / `--no-use-cache` | `PICKLE_PRETALX_TALKS` setting   | Cache fetched submissions on disk for faster repeated local runs (dev only).                           |
| `--image-format`                 | `webp` (choices: `webp`, `jpeg`) | Output format for generated talk images.                                                               |

The `--detect-only` mode and the default mutation mode are opposites: the former records intent, the
latter applies it. See the [modes table](pretalx-sync.md#modes) for the full behavior matrix.

### Required configuration

- `PRETALX_API_TOKEN` must be set (or passed via `--api-token`). Required.
- `DEFAULT_EVENT` is used as the fallback event slug when `--event-slug` is omitted, and by the
    admin "Check Pretalx now" button.

### Dry-run support

Yes. `--dry-run` logs every create/update/delete it would perform and never writes to the database.
`--detect-only` is a separate safe mode that does write `PendingPretalxChange` audit rows but leaves
the live `Talk` / `Speaker` / `Room` graph untouched.

### Example invocations

=== "First import of a new event"

    ```bash
    uv run python manage.py import_pretalx_talks \
      --pretalx-event-url https://pretalx.com/pyconde-pydata-2026/ \
      --event-slug pyconde-pydata-2026 \
      --event-name "PyCon DE & PyData 2026"
    ```

=== "Dry run against the default event"

    ```bash
    uv run python manage.py import_pretalx_talks --dry-run
    ```

=== "Detect changes for admin review"

    ```bash
    uv run python manage.py import_pretalx_talks \
      --detect-only --event-slug pyconde-pydata-2026
    ```

=== "Refresh social cards after a template change"

    ```bash
    uv run python manage.py import_pretalx_talks --force-images
    ```

## `import_livestream_urls`

Imports per-room livestream (Vimeo embed) sessions from a Google Sheet that lists each room's stream
windows. Each row becomes a `Streaming` record tied to a `Room`. The talk dashboard uses these to
show "watch live" links during the session window.

### Purpose

- Read a published Google Sheet (downloaded as xlsx) of room streaming windows.
- Keep only Vimeo rows that have an embed link and a valid start and end time.
- Replace the target event's existing `Streaming` rows with the ones from the sheet.

### Usage

```bash
uv run python manage.py import_livestream_urls \
  --livestreams-sheet-id SHEET_ID \
  --livestreams-worksheet-name Sheet1
```

The whole command runs inside a single database transaction, so a failure rolls back cleanly.

!!! warning "This replaces, it does not merge"

    The import deletes existing streamings before inserting the new ones. With `--event-slug` set (the
    default, from `DEFAULT_EVENT`), only that event's streamings are wiped, so importing one event's
    sheet cannot clear another event's livestreams. With no slug at all (and `DEFAULT_EVENT` unset), the
    delete is global. A slug that does not resolve to an existing event is treated as an operator error
    and aborts the run rather than silently widening the delete.

### Flags

| Flag                           | Default                              | What it does                                                                                                |
| ------------------------------ | ------------------------------------ | ----------------------------------------------------------------------------------------------------------- |
| `--livestreams-sheet-id`       | `LIVESTREAMS_SHEET_ID` setting       | Google Sheets ID for the livestreams sheet.                                                                 |
| `--livestreams-worksheet-name` | `LIVESTREAMS_WORKSHEET_NAME` setting | Name of the worksheet within the sheet.                                                                     |
| `--event-slug`                 | `DEFAULT_EVENT` setting              | Scope rooms and replaced streamings to this event. Required when a room name exists in more than one event. |
| `--dry-run`                    | off                                  | Report what would be imported without touching the database.                                                |

### Required configuration

- `LIVESTREAMS_SHEET_ID` and `LIVESTREAMS_WORKSHEET_NAME` (or the matching flags). The sheet must be
    published / shared so the xlsx export URL is reachable without authentication.
- The rooms named in the sheet must already exist in the target event. A row whose room is not found
    is skipped with a warning, not created. Rooms are normally created by `import_pretalx_talks`, so
    run that first.

### Dry-run support

Yes. `--dry-run` prints each row it would process (matched room, start, end) and a count of rows it
would skip because their room was not found. No streamings are deleted or created.

### Expected sheet columns

The cleaner keeps rows where `Vimeo / Restream` equals `Vimeo` and the `Embed Link` is non-empty,
then uses these columns:

- `Room` - matched against an existing `Room.name` (scoped to the event when one is given).
- `Start Time` and `End Time` - parsed as `Europe/Berlin` local time. Blank or unparseable cells are
    dropped (with a warning) rather than aborting the whole import.
- `Embed Link` - stored as the streaming's video link.

### Example invocations

=== "Import the default event's streams"

    ```bash
    uv run python manage.py import_livestream_urls \
      --livestreams-sheet-id 1AbC...XyZ \
      --livestreams-worksheet-name "Streams"
    ```

=== "Preview without writing"

    ```bash
    uv run python manage.py import_livestream_urls --dry-run
    ```

=== "Scope to a specific event"

    ```bash
    uv run python manage.py import_livestream_urls \
      --event-slug pyconde-pydata-2026
    ```

## `update_video_links`

Backfills recorded-video links onto talks once the conference recordings are uploaded to Vimeo. It
fetches every video in the configured Vimeo project folders and matches each one to a talk by its
Pretalx code.

### Purpose

- List all videos in one or more Vimeo project folders (paginated, 100 per page).
- Match each video to a talk. The match assumes the video name starts with the Pretalx code,
    followed by a `-` separator and the title (`{pretalx_code}-{title}`).
- Set the matched talk's `video_link` to the Vimeo player embed URL and reset `video_start_time` to
    `0`.

### Usage

```bash
uv run python manage.py update_video_links \
  --vimeo-access-token TOKEN \
  --vimeo-project-ids 123,456
```

Runs inside a single transaction.

!!! note "Matching is exact on the Pretalx code"

    The Pretalx code is parsed from the part of the video name before the first `-`. A candidate talk is
    only updated when its `pretalx_code` matches that prefix exactly, so a short code does not clobber a
    talk whose code merely starts with the same characters. When more than one talk matches a code, the
    command skips it with a warning rather than overwriting an arbitrary one.

### Flags

| Flag                   | Default                      | What it does                                      |
| ---------------------- | ---------------------------- | ------------------------------------------------- |
| `--vimeo-access-token` | `VIMEO_ACCESS_TOKEN` setting | Vimeo access token.                               |
| `--vimeo-project-ids`  | `VIMEO_PROJECT_IDS` setting  | Comma-separated list of Vimeo project IDs.        |
| `--dry-run`            | off                          | Fetch and count videos without updating any talk. |

### Required configuration

- `VIMEO_ACCESS_TOKEN` (or `--vimeo-access-token`).
- `VIMEO_PROJECT_IDS` (or `--vimeo-project-ids`) listing the project folders that hold the talk
    recordings.

### Dry-run support

Yes. `--dry-run` fetches the videos and reports how many it found, but does not update any talk.

### Example invocations

=== "Update from configured projects"

    ```bash
    uv run python manage.py update_video_links \
      --vimeo-access-token "$VIMEO_ACCESS_TOKEN" \
      --vimeo-project-ids 123,456
    ```

=== "Preview the fetch"

    ```bash
    uv run python manage.py update_video_links --dry-run
    ```

## `generate_fake_talks`

Generates realistic fake talks, speakers, rooms, and streaming sessions for local development and
testing. It is run automatically by the dev setup script when `GEN_FAKE_DATA=true`. The generated
schedule deliberately includes a finished talk, a talk happening right now, and an upcoming talk so
the dashboard's time-based states are easy to see.

### Purpose

- Build an event (a synthetic `fake-event` when no `--event-slug` is given) with rooms in three
    categories: plenary, talks, and tutorials.
- Create streaming sessions across the conference days, guaranteeing at least one session covers the
    current moment.
- Generate a pool of speakers and assign 1-3 of them to each talk.
- Create talks at conflict-free time slots with plausible titles, tracks, durations, and optional
    Slido/video links.

### Usage

```bash
uv run python manage.py generate_fake_talks
```

With no flags it creates 100 talks over 3 days starting yesterday, scoped to `DEFAULT_EVENT` (or the
synthetic `fake-event` when that setting is empty).

### Flags

| Flag                 | Default                                               | What it does                                                                       |
| -------------------- | ----------------------------------------------------- | ---------------------------------------------------------------------------------- |
| `--count`            | `100`                                                 | Number of talks to generate.                                                       |
| `--date`             | yesterday (`YYYY-MM-DD`)                              | Base conference date. The first day starts at 09:00 local time.                    |
| `--seed`             | `None`                                                | Optional integer seed for deterministic data generation.                           |
| `--clear-existing`   | off                                                   | Delete all existing rooms, talks, speakers, and streamings before generating.      |
| `--talk-video-prob`  | `0.3`                                                 | Probability `[0-1]` a talk gets a custom `video_link` when its room has streaming. |
| `--slido-prob`       | `0.3`                                                 | Probability `[0-1]` a talk gets a custom `slido_link`.                             |
| `--video-start-prob` | `0.1`                                                 | Probability `[0-1]` a talk gets a custom `video_start_time` offset in seconds.     |
| `--days`             | `3`                                                   | Number of conference days to generate.                                             |
| `--tracks`           | built-in track list                                   | Comma-separated list of tracks.                                                    |
| `--rooms-plenary`    | `Spectrum`                                            | Comma-separated list of plenary (keynote) rooms.                                   |
| `--rooms-talks`      | `Titanium,Helium,Platinum,Europium,Hassium,Palladium` | Comma-separated list of talk rooms.                                                |
| `--rooms-tutorials`  | `Ferrum,Dynamicum`                                    | Comma-separated list of tutorial rooms.                                            |
| `--event-slug`       | `DEFAULT_EVENT` setting                               | Event slug to associate generated data with. Falls back to `fake-event`.           |
| `--event-name`       | `""`                                                  | Human-readable name used when creating a new `Event`.                              |

The built-in default track list is: MLOps & DevOps, Security, Django & Web, Natural Language
Processing, Machine Learning, Data Handling & Engineering, Computer Vision, and Programming &
Software Engineering.

### Required configuration

None. The command needs no API tokens or external services. It only depends on the `faker` package,
which is part of the project dependencies.

### Dry-run support

No. This command does not have a `--dry-run` flag because it only writes to a local development
database. Use `--clear-existing` to start from a clean slate, and `--seed` for reproducible runs.

### Example invocations

=== "Default dataset"

    ```bash
    uv run python manage.py generate_fake_talks
    ```

=== "Reproducible small dataset"

    ```bash
    uv run python manage.py generate_fake_talks --count 20 --seed 42 --clear-existing
    ```

=== "Custom dates and rooms"

    ```bash
    uv run python manage.py generate_fake_talks \
      --date 2026-04-22 --days 4 \
      --rooms-talks "Room A,Room B,Room C"
    ```

=== "Scope to a named event"

    ```bash
    uv run python manage.py generate_fake_talks \
      --event-slug demo-2026 --event-name "Demo 2026"
    ```

## See also

- [Pretalx sync](pretalx-sync.md) for the full importer operator manual.
- The command sources live under
    [`talks/management/commands/`](https://github.com/PioneersHub/pyconde-talks/blob/main/talks/management/commands).
