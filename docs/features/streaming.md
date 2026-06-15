---
icon: lucide/video
---

# Live streaming

During the event each room can carry a live video stream, and after the event each talk can carry
its own recording. Both surface in the same place: the video embed on the talk detail page. The app
picks the right URL automatically based on the talk's room and time slot, so organizers rarely have
to touch a talk by hand.

Source: models `Streaming` and `Talk` in
[`talks/models.py`](https://github.com/PioneersHub/pyconde-talks/blob/main/talks/models.py),
importers
[`import_livestream_urls.py`](https://github.com/PioneersHub/pyconde-talks/blob/main/talks/management/commands/import_livestream_urls.py)
and
[`update_video_links.py`](https://github.com/PioneersHub/pyconde-talks/blob/main/talks/management/commands/update_video_links.py).

## The two sources of video

A talk can show video from two places, and they are resolved in a fixed order by
`Talk.get_video_link()`:

1. **The talk's own `video_link`**, if set. This always wins. It is used for a finished recording
    pinned directly to the talk.
2. **The covering streaming session**, otherwise. A `Streaming` belongs to a room and has a start
    time, an end time, and a video link. A talk matches a streaming when that streaming covers its
    slot in the same room.

If neither resolves, the talk shows no video. Upcoming talks hide their link unless the
`SHOW_UPCOMING_TALKS_LINKS` setting is enabled, so a stream URL does not leak before the session
starts.

### How a talk is matched to a streaming

The match (the `Talk.streaming` cached property) looks for a streaming in the same room that starts
no later than one minute into the talk (`STREAMING_START_MARGIN`) and runs through at least the
first half of the talk. Requiring half-coverage prevents a short, unrelated stream from being
mistaken for the talk's stream. The first matching streaming wins, ordered by start time.

Because `streaming` is a cached property, every consumer on a page (the video link, the
transcription URL, the start-time offset, the "is it live" check) shares one query per talk. List
and grid views pre-warm that cache in a single batch query with `with_streamings()` to avoid an N+1.

### Start-time offset

A streaming usually covers several back-to-back talks, so the talk does not start at second zero of
the video. `get_video_start_time()` returns the talk's explicit `video_start_time` if set, otherwise
the number of seconds between the streaming start and the talk start. The detail page uses this to
offer a "Jump to" button (for recordings) or a manual-skip hint (for a still-live stream).

## Provider detection and embedding

The embed is provider-aware. `Talk.video_provider` inspects the resolved link and returns `Youtube`
(for both `youtube.com` and `youtu.be`) or `Vimeo`. The detail template then loads the matching
player script (the Vimeo or YouTube iframe API) so the "Jump to" button can seek the player.

YouTube links get `enablejsapi=1` appended automatically when the talk is saved
(`_enrich_video_link`), which the player API needs to accept seek commands. The step is idempotent,
so re-saving does not keep appending it. The `video_link` field is also validated on save by
`validate_video_link`.

A live stream is detected with `has_active_streaming()`: true when the matched streaming's window
contains the current time. The detail page uses this to switch the start-of-talk control from a
seekable "Jump to" button to a "please skip manually" note, since you cannot reliably seek a live
feed.

## Importing live stream URLs

`import_livestream_urls` pulls streaming sessions from a Google Sheet and replaces the existing
`Streaming` rows.

It reads an `.xlsx` export of the sheet (configured by `LIVESTREAMS_SHEET_ID` and
`LIVESTREAMS_WORKSHEET_NAME`), keeps only rows marked `Vimeo` that have an embed link and a usable
start and end time, and creates one `Streaming` per surviving row matched to a room by name.

Key behaviors to know:

- **Event scoping**: `--event-slug` (default `DEFAULT_EVENT`, i.e. `pyconde-pydata-2026`) scopes
    both the room lookup and the replace step. Only that event's streamings are deleted and
    re-imported, so importing one event's sheet cannot wipe another event's streams. A slug that
    does not resolve is an error, not a silent widening of the delete.
- **Time handling**: times are parsed as `Europe/Berlin` wall-clock, with malformed cells coerced to
    "not a time" and then dropped, so one bad row does not abort the whole atomic import. Rows with
    a missing start or end are skipped (the model requires both and enforces `start < end`).
- **Unmatched rooms**: a row whose room name is not found is skipped with a warning rather than
    failing the import.
- **Dry run**: `--dry-run` prints what would be imported without writing.

The whole command runs in a transaction, so a failure rolls back cleanly.

## Updating recording links after the event

`update_video_links` pulls finished recordings from Vimeo and pins them to talks as their own
`video_link`, which then takes precedence over any streaming fallback.

It fetches every video in the configured Vimeo projects (`VIMEO_ACCESS_TOKEN`, comma-separated
`VIMEO_PROJECT_IDS`), paging through the API, and reads the Pretalx code from the start of each
video name (everything before the first `-`). It then finds the talk whose Pretalx code matches
exactly.

The match is deliberately exact, not a substring: a candidate set is narrowed by
`pretalx_link__contains`, but the final match compares `Talk.pretalx_code` for equality. This stops
a short code such as `ABC` from clobbering a talk whose code merely starts with it (`ABCDEF`), and
an ambiguous match is skipped rather than overwriting an arbitrary talk. When a talk matches, its
`video_link` is set and `video_start_time` is reset to 0 (the recording is already trimmed to the
talk). `--dry-run` reports the counts without writing.

## Transcriptions

Transcriptions follow the same fallback shape as video: `get_transcription_url()` returns the talk's
own `transcription_url` if set, otherwise the `transcription_url` of the matched streaming session.
When present, the detail page shows the transcription in a collapsible iframe and as a link.
