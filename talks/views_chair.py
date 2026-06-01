"""
Session-chair views (moderator-only).

Lets a moderator volunteer (or step down) as the session chair for a block of adjacent talks in
the same room and renders a day grid (times on the left, rooms across the top) showing who is
chairing each session.
"""

from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING, Any, cast

from django.core.exceptions import PermissionDenied
from django.db.models.functions import TruncDate
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST, require_safe

from events.session import resolve_default_event

from .models import FAR_FUTURE, Room, Talk
from .utils import is_htmx_request
from .views_qa import is_moderator


if TYPE_CHECKING:
    from django.http import HttpRequest, HttpResponse

    from users.models import CustomUser


# Talks in the same room are treated as one block when the gap between them is at most this long.
BLOCK_GAP_TOLERANCE = timedelta(minutes=5)


def _require_moderator(user: CustomUser) -> None:
    """Raise PermissionDenied unless the user is a moderator."""
    if not is_moderator(user):
        raise PermissionDenied


def _parse_chair_date(date_str: str | None) -> date | None:
    """Parse a YYYY-MM-DD string into a date, returning None on failure."""
    if not date_str:
        return None
    try:
        return datetime.strptime(date_str, "%Y-%m-%d").date()  # noqa: DTZ007
    except ValueError:
        return None


def _talk_block(talk: Talk, user: CustomUser) -> list[Talk]:
    """
    Return the contiguous block of talks the given talk belongs to.

    A block is the run of talks in the same room and event whose neighbors are no more than
    ``BLOCK_GAP_TOLERANCE`` apart, so a moderator chairs a whole stretch of back-to-back sessions
    (for example a series of lightning talks) in one click.
    """
    if not talk.room or not talk.start_time:
        return [talk]

    day_talks = list(
        Talk.objects.filter(
            room=talk.room,
            event_id=talk.event_id,
            start_time__date=talk.start_time.date(),
        )
        .exclude(start_time__year=FAR_FUTURE.year)
        .accessible_to(user)
        .order_by("start_time"),
    )

    try:
        index = next(i for i, t in enumerate(day_talks) if t.pk == talk.pk)
    except StopIteration:
        return [talk]

    # Expand left while the previous talk ends within tolerance of the current one's start.
    start = index
    while start > 0:
        prev = day_talks[start - 1]
        prev_end = prev.start_time + prev.duration
        if day_talks[start].start_time - prev_end > BLOCK_GAP_TOLERANCE:
            break
        start -= 1

    # Expand right while the next talk starts within tolerance of the current one's end.
    end = index
    while end < len(day_talks) - 1:
        cur_end = day_talks[end].start_time + day_talks[end].duration
        if day_talks[end + 1].start_time - cur_end > BLOCK_GAP_TOLERANCE:
            break
        end += 1

    return day_talks[start : end + 1]


@require_POST
def toggle_session_chair(request: HttpRequest, talk_id: int) -> HttpResponse:
    """
    Claim (or release) the current moderator as chair for a talk's whole block.

    A block can only have one chair at a time. A moderator may claim a free block or release a
    block they already chair, but cannot take over a block someone else is chairing.
    """
    user = cast("CustomUser", request.user)
    _require_moderator(user)

    talk = get_object_or_404(Talk.objects.accessible_to(user), pk=talk_id)
    block = _talk_block(talk, user)

    # Only the chair (or nobody) may change a block; never override another moderator.
    if talk.session_chair_id in (None, user.pk):
        new_chair = None if talk.session_chair_id == user.pk else user
        for block_talk in block:
            if block_talk.session_chair_id in (None, user.pk):
                block_talk.session_chair = new_chair
                block_talk.save(update_fields=["session_chair", "updated_at"])

    selected_date = talk.start_time.date() if talk.start_time else None
    selected_event = request.POST.get("event", "")

    if is_htmx_request(request):
        event_id = int(selected_event) if selected_event.isdigit() else None
        context = _grid_context(user, event_id, selected_date)
        return render(request, "talks/partials/chair_grid_table.html", context)

    url = reverse("chair_grid")
    params = []
    if selected_date:
        params.append(f"date={selected_date.isoformat()}")
    if selected_event:
        params.append(f"event={selected_event}")
    if params:
        url = f"{url}?{'&'.join(params)}"
    return redirect(url)


def _chair_dates(user: CustomUser, event_id: int | None) -> list[date]:
    """Return the available schedule dates for the chair grid, scoped to the user's access."""
    talks_qs = Talk.objects.exclude(start_time__year=FAR_FUTURE.year).accessible_to(user)
    if event_id:
        talks_qs = talks_qs.filter(event_id=event_id)
    date_qs = (
        talks_qs.annotate(d=TruncDate("start_time"))
        .values_list("d", flat=True)
        .distinct()
        .order_by("d")
    )
    return list(date_qs)


def _resolve_event_id(request: HttpRequest) -> int | None:
    """Return the event id to scope the grid by, or None for a cross-event view."""
    event_param = request.GET.get("event", "")
    if event_param:
        return int(event_param) if event_param.isdigit() else None
    default_event = resolve_default_event(request)
    return default_event.pk if default_event else None


def _resolve_selected_date(request: HttpRequest, available_dates: list[date]) -> date | None:
    """Pick the chair grid date: user's ?date, then today, then the first available date."""
    selected = _parse_chair_date(request.GET.get("date"))
    if selected in available_dates:
        return selected
    today = timezone.localdate()
    if today in available_dates:
        return today
    return available_dates[0] if available_dates else None


def _assign_block_ids(talks: list[Talk]) -> None:
    """
    Tag each talk with a ``block_id`` attribute identifying its contiguous block.

    Talks in the same room whose neighbors are within ``BLOCK_GAP_TOLERANCE`` share a block id, so
    the grid can highlight (and chair) a whole stretch of back-to-back sessions together. The id is
    a transient view-only attribute, never persisted.
    """
    talks_by_room: dict[int, list[Talk]] = {}
    for talk in talks:
        if talk.room:
            talks_by_room.setdefault(talk.room_id, []).append(talk)

    for room_id, room_talks in talks_by_room.items():
        room_talks.sort(key=lambda t: t.start_time)
        block_index = 0
        prev_end: datetime | None = None
        for talk in room_talks:
            if prev_end is not None and talk.start_time - prev_end > BLOCK_GAP_TOLERANCE:
                block_index += 1
            talk.block_id = f"{room_id}-{block_index}"  # type: ignore[attr-defined]
            prev_end = talk.start_time + talk.duration


def _build_chair_grid(
    selected_date: date,
    user: CustomUser,
    event_id: int | None,
) -> tuple[list[Room], list[dict[str, Any]]]:
    """
    Build the chair grid for a day.

    Returns ``(rooms, rows)`` where each row groups the talks that share a start time, with one
    cell per room (or ``None`` when that room has no talk in that slot).
    """
    talks_qs = (
        Talk.objects.filter(start_time__date=selected_date)
        .exclude(start_time__year=FAR_FUTURE.year)
        .accessible_to(user)
        .select_related("room", "session_chair")
        .order_by("start_time", "room__name")
    )
    if event_id:
        talks_qs = talks_qs.filter(event_id=event_id)
    talks = list(talks_qs)

    # Unique rooms ordered by name, preserving only rooms that host a talk this day.
    rooms_by_id: dict[int, Room] = {}
    for talk in talks:
        if talk.room and talk.room_id not in rooms_by_id:
            rooms_by_id[talk.room_id] = talk.room
    rooms = sorted(rooms_by_id.values(), key=lambda r: r.name)

    # Tag each talk with the id of the contiguous block it belongs to, so the UI can highlight a
    # whole block together (the same grouping used when assigning a chair).
    _assign_block_ids(talks)

    # Group talks by start time, then place each into its room column.
    rows: list[dict[str, Any]] = []
    talks_by_start: dict[datetime, dict[int, Talk]] = {}
    for talk in talks:
        if not talk.room:
            continue
        talks_by_start.setdefault(talk.start_time, {})[talk.room_id] = talk

    for start_time in sorted(talks_by_start):
        room_talks = talks_by_start[start_time]
        cells = [room_talks.get(room.id) for room in rooms]
        rows.append({"start_time": start_time, "cells": cells})

    return rooms, rows


def _grid_context(
    user: CustomUser,
    selected_event_id: int | None,
    selected_date: date | None,
) -> dict[str, Any]:
    """Build the template context shared by the full grid page and the HTMX table fragment."""
    available_dates = _chair_dates(user, selected_event_id)
    rooms: list[Room] = []
    rows: list[dict[str, Any]] = []
    if selected_date:
        rooms, rows = _build_chair_grid(selected_date, user, selected_event_id)

    years = {d.year for d in available_dates}
    return {
        "available_dates": available_dates,
        "selected_date": selected_date,
        "has_multiple_years": len(years) > 1,
        "rooms": rooms,
        "rows": rows,
        "has_grid": bool(rows),
        "events": user.visible_events(),
        "selected_event": str(selected_event_id) if selected_event_id else "",
        "current_user_id": user.pk,
    }


@require_safe
def chair_grid_view(request: HttpRequest) -> HttpResponse:
    """Render the moderator-only session-chair day grid: times left, rooms across the top."""
    user = cast("CustomUser", request.user)
    _require_moderator(user)

    selected_event_id = _resolve_event_id(request)
    available_dates = _chair_dates(user, selected_event_id)
    selected_date = _resolve_selected_date(request, available_dates)

    context = _grid_context(user, selected_event_id, selected_date)
    return render(request, "talks/chair_grid.html", context)
