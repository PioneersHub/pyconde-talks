---
icon: lucide/star
---

# Ratings

Attendees can rate any talk they have access to from one to five stars and, optionally, leave a
written comment. Stars give organizers a quick signal of how a talk landed; comments are private
feedback that only organizers ever read.

Source:
[`talks/views_rating.py`](https://github.com/PioneersHub/pyconde-talks/blob/main/talks/views_rating.py),
model
[`talks/models_rating.py`](https://github.com/PioneersHub/pyconde-talks/blob/main/talks/models_rating.py),
admin
[`talks/admin_rating.py`](https://github.com/PioneersHub/pyconde-talks/blob/main/talks/admin_rating.py).

## The rating model

A `Rating` row ties one user to one talk with a score, an optional comment, and timestamps. Two
database constraints keep the data honest:

- A unique `(talk, user)` constraint: a user has at most one rating per talk. Re-rating updates the
    existing row instead of creating a second one.
- A check constraint that the score is between 1 and 5 (`MIN_RATING_SCORE` and `MAX_RATING_SCORE`).

The comment is a free-text field capped at `COMMENT_MAX_LENGTH` (2000 characters). Its help text
marks it as visible only to admins, and the application enforces that boundary (see
[Comment privacy](#comment-privacy)).

## Rating a talk

The star widget lives on the talk detail page
([`templates/talks/partials/rating_widget.html`](https://github.com/PioneersHub/pyconde-talks/blob/main/templates/talks/partials/rating_widget.html)).
The flow:

1. The attendee clicks a star. The widget posts the score to `rate_talk`.
2. The view validates the score (a non-integer or out-of-range value returns a 422 for HTMX, or a
    flashed error otherwise) and creates or updates the rating with `update_or_create`.
3. Once a rating exists, a comment box appears. Saving the comment posts again with
    `save_comment=1`.
4. A trash button deletes the rating (`delete_rating`), guarded by a confirmation prompt.

Star clicks and comment saves are deliberately separated. A star click updates only the score and
preserves any in-progress comment text as a draft, so clicking a different star never wipes a
comment the attendee is still typing. The comment is only written to the database when explicitly
saved.

### HTMX flow

Every rating interaction is HTMX-driven and swaps the `#rating-widget` element in place, so the page
never reloads. The response also carries an out-of-band swap of the star summary near the talk title
(`title_star_rating_oob.html`), so both the widget and the header stars update from a single
request.

For a non-HTMX client the same endpoints flash a success or error message and redirect back to the
detail page, so the feature still works without JavaScript.

There is also a read-only JSON endpoint, `get_talk_rating_stats`, returning the average, the count,
and the current user's rating. It respects the same visibility rules as the rendered summary.

## Visibility of the aggregate

The average and count are not always shown. Visibility is decided by `_can_see_rating_summary`:

- Moderators (staff or superusers) always see the summary.
- Other users see it only when the talk's event has `show_rating_summary` enabled.
- In the cross-event ("all events") scope there is no single event to read the flag from, so the
    summary defaults to hidden rather than bypassing a per-event opt-out.

When the summary is hidden, the API and template are handed a `None` average and a zero count, so
the numbers cannot leak through a side channel. Attendees can still submit and see their own rating
regardless of this flag.

The `star_rating` template tag (from the `rating_tags` library) renders the display: it rounds the
average to the nearest half star and draws full, half, and empty star SVGs, followed by the numeric
average and the count. With no ratings it renders "No ratings yet".

## Comment privacy

Rating comments are private feedback for organizers. They are never shown to other attendees.

- In the Django admin, the full comment is visible to staff on the `Rating` change page.
- On the public site, the talk detail page exposes a "Rating comments" section only to superusers,
    listing each comment with the rater's email, score, and timestamp, newest first.

This split lets organizers read candid feedback while keeping it out of the attendee-facing UI.

## Moderation and statistics in the admin

The `RatingAdmin` changelist shows the talk, user, score, a "has comment" boolean, and the creation
date. It supports:

- **Filtering** by score, by creation date, and by whether a comment is present. The "Has comment"
    filter is a custom filter (`HasCommentFilter`) with "With comment" and "Without comment"
    options.
- **Searching** by talk title, user email, and comment text.

The list query uses `list_select_related` on the talk and user so the changelist does not run an
extra query per row.

!!! note "Aggregate stats for talk lists"

    The talk list and upcoming-talks views annotate each talk with `average_rating` and `rating_count`
    in one pass via `TalkQuerySet.with_rating_stats()`. The count uses `distinct=True` so that a search
    joining the speakers table does not inflate the rating total by counting each rating once per
    speaker. The detail page instead uses `Talk.get_rating_stats()`, a single aggregate query for one
    talk.
