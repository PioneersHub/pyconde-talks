---
icon: lucide/presentation
---

# Talks

The talks area is the heart of the attendee experience. It lists every talk an attendee may see,
lets them search and filter, and links each row to a detail page with the abstract, links, and (once
available) the video. Everything is scoped to the events a user has access to, so an attendee never
sees a talk from an event they did not register for.

All talk views require authentication. A logged-out visitor is redirected to the login flow before
any of these pages render.

## Access scoping

Every query that drives these pages runs through `Talk.objects.accessible_to(user)`. Superusers see
every talk; everyone else sees only talks whose event they belong to
(`event__in=user.events.all()`). Because `Talk.event` is required, there is no event-less talk that
could slip past the filter.

See
[`TalkQuerySet.accessible_to`](https://github.com/PioneersHub/pyconde-talks/blob/main/talks/models.py).

## Talk list and dashboard

The talk list (`TalkListView`, served at `/`) is the default landing page after login. It renders a
single, scrollable column of talk cards sorted by start time. Each card shows the title, speakers, a
timing badge, date and room, presentation type and track, and a row of action buttons.

Source: [`talks/views.py`](https://github.com/PioneersHub/pyconde-talks/blob/main/talks/views.py),
template
[`templates/talks/talk_list.html`](https://github.com/PioneersHub/pyconde-talks/blob/main/templates/talks/talk_list.html).

### Timing badges

Each card is styled by the talk's timing relative to now, computed by `Talk.get_timing()` with a
five-minute margin:

- **Happening now** (orange): the current time falls within the talk's slot (plus or minus the
    margin).
- **Upcoming** (blue): the talk starts more than five minutes from now.
- **Completed** (gray): the talk ended more than five minutes ago.

A colored status bar on the left edge of each card mirrors the badge.

### Filters

The filter bar offers, in this order, dropdowns for event, day, room, presentation type, and track,
plus a status dropdown and a "Saved only" checkbox. The filter options are not hard-coded: they are
derived from the talks actually visible in the selected event and search scope, so an empty room or
a track with no talks never appears.

| Filter            | GET param           | Behavior                                              |
| ----------------- | ------------------- | ----------------------------------------------------- |
| Event             | `event`             | Numeric event id, or `all` for every accessible event |
| Day               | `date`              | ISO date (`YYYY-MM-DD`)                               |
| Room              | `room`              | Numeric room id                                       |
| Presentation type | `presentation_type` | One of the `Talk.PresentationType` values             |
| Track             | `track`             | Track name                                            |
| Status            | `status`            | `current`, `upcoming`, or `completed`                 |
| Saved only        | `saved`             | `1` to show only the user's bookmarked talks          |

Room, day, track, and type are validated against the event-scoped queryset before they are applied,
and then combined into a single `.filter()`. This matters in two ways:

- A stale param left over from a previous event (for example a room id that belongs to another
    event) is silently dropped rather than producing a confusing empty list.
- Intersecting criteria are applied together, so "Room A + April 6" correctly returns nothing when
    no talk matches both, instead of widening the result.

The status filter reuses the same five-minute margin as the timing badges: `current` matches talks
whose slot overlaps now, `upcoming` matches talks starting after the margin, and `completed` matches
talks that ended before it.

!!! info "When no event is selected"

    An empty or garbage `event` param (for example `?event=abc`) falls back to the resolved default
    event for the session rather than showing every event. The default event slug for this deployment is
    `pyconde-pydata-2026`.

### Full-text search

The search box queries across three scopes, toggled by the Title, Description, and Author
checkboxes:

- **Title**: `title__icontains`.
- **Description**: matches `description` or `abstract` (both fields).
- **Author**: matches any speaker name (`speakers__name__icontains`).

When no scope is checked (or the special `all` scope is used) all three are searched. Matched terms
are highlighted in the title and speaker name via the `highlight` template filter.

Search and filters compose: the search narrows the queryset first, then the filters apply on top.

### Dashboard statistics

The `dashboard_stats` view renders a small per-event summary used on the dashboard: total talks,
today's talks, and the number of talks with an available recording. A single event renders as three
stat cards (via the `stat_card` inclusion tag); multiple events render as a per-event table with a
totals row. The "available recordings" count walks each talk's `get_video_link()`, so it reflects
both talks with their own `video_link` and talks covered by a live streaming session.

The response is cached for 60 seconds and varies on the session cookie, so the count stays cheap
without leaking one user's event scope to another.

### Upcoming talks

The `upcoming_talks` view returns the next eight talks starting after now, scoped to the user's
events, with Today/Tomorrow badges. It is cached for 30 seconds and varies on the cookie.

## Talk detail page

The detail page (`TalkDetailView`, at `/<id>/`) shows everything about one talk. The template is
[`templates/talks/talk_detail.html`](https://github.com/PioneersHub/pyconde-talks/blob/main/templates/talks/talk_detail.html).

It includes:

- **Header**: title, speaker names, and a bookmark button. The session chair line appears only for
    moderators, and only when a chair is assigned.
- **Metadata box**: date, start and end time, room, presentation type badge, and track badge.
- **Video embed**: when `get_video_link()` returns a URL, the talk is shown in a responsive 16:9
    iframe. See [Live streaming](streaming.md) for where that link comes from.
- **Jump-to-start control**: for recordings with a computed start offset, a "Jump to" button seeks
    the player to the estimated start of the talk. For a still-live stream it shows a manual-skip
    hint instead. The seek is wired up with the Vimeo or YouTube player API depending on the
    provider.
- **Transcription**: a collapsible iframe plus a link, shown when `get_transcription_url()` resolves
    (from the talk or its streaming session).
- **Description**: the full description, rendered from Markdown.
- **Rating widget**: see [Ratings](ratings.md).
- **Rating comments**: a superuser-only section listing attendee comments, ordered newest first.
- **Action buttons**: "View on Pretalx", "Questions & Answers" (see
    [Questions and answers](questions.md)), and a transcription link when available.

### Rating summary visibility

Whether the aggregate star rating shows on the detail and list pages is controlled per event by
`Event.show_rating_summary`, with moderators always allowed to see it. When the "all events" scope
is selected there is no single event to read the flag from, so the summary defaults to hidden rather
than silently bypassing a per-event opt-out.

### ID or Pretalx redirect

`talk_redirect_view` resolves a path segment that may be either a numeric talk id or a Pretalx
submission code, and redirects to the canonical detail URL. This lets a Pretalx link such as
`/ABCDEF/` land on the right talk. The same pattern exists for the Q&A page.

## Saved talks and bookmarks

Any attendee can bookmark a talk. Saved talks are stored in the `SavedTalk` model, a simple join
between a user and a talk with a unique `(user, talk)` constraint so a talk can only be saved once.

Source:
[`talks/views_saved.py`](https://github.com/PioneersHub/pyconde-talks/blob/main/talks/views_saved.py),
model
[`talks/models_rating.py`](https://github.com/PioneersHub/pyconde-talks/blob/main/talks/models_rating.py).

The `toggle_save_talk` endpoint flips the bookmark: if the talk is already saved it is removed,
otherwise it is created. It accepts only POST. The bookmark icon appears on the detail page, every
list card, and every schedule card.

To keep list rendering fast, the views build a set of the current user's saved talk ids once per
page (`SavedTalk.talk_ids_for`) and the template tests membership with the `is_in` filter rather
than querying per row.

!!! tip "Saved-tags helper"

    The `saved_tags` template library exposes a single `is_in` filter used as
    `{{ talk.pk|is_in:saved_talk_ids }}`. It returns `False` (instead of raising) when the container is
    not iterable, so a template never breaks on an unexpected value.

## HTMX-driven updates

The talk pages lean on HTMX so common interactions happen without a full page reload.

- **Search and filters**: both forms issue `hx-get` requests to the list view and swap only the
    `#talks-container`. The view detects the HTMX request and returns the `#talk-list` template
    fragment instead of the whole page. Typing in the search box debounces 300 ms; changing any
    dropdown fires immediately.
- **Filter sync**: when the event changes, the list fragment re-renders every other dropdown
    out-of-band (`hx-swap-oob`) so the day, room, type, track, and status options stay consistent
    with the new event.
- **URL push**: `hx-push-url="true"` keeps the address bar in sync, so a filtered view is
    bookmarkable and survives a refresh.
- **Auto-refresh**: the container re-fetches every 300 seconds so timing badges (Happening now /
    Upcoming / Completed) stay current during the event.
- **Bookmark toggle**: the save button posts to `toggle_save_talk` and swaps itself with the updated
    state. Schedule cards use a compact icon-only partial; list and detail pages use the labeled
    button. The view picks the partial based on the `HX-Target` header.

For non-HTMX clients every endpoint degrades gracefully: the bookmark toggle flashes a message and
redirects back to the detail page.
