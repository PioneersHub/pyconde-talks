"""Views for the Pretalx-style CSS Grid schedule."""

from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING, Any, cast

from django.db.models.functions import TruncDate
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.http import require_safe

from events.session import resolve_default_event

from .models import FAR_FUTURE, Room, SavedTalk, Talk, prefetch_streamings


if TYPE_CHECKING:
    from django.http import HttpRequest, HttpResponse

    from users.models import CustomUser


def _parse_schedule_date(date_str: str | None) -> date | None:
    """Parse a YYYY-MM-DD string into a date, returning None on failure."""
    if not date_str:
        return None
    try:
        return datetime.strptime(date_str, "%Y-%m-%d").date()  # noqa: DTZ007
    except ValueError:
        return None


def _build_grid_slices(
    talks: list[Talk],
) -> tuple[list[datetime], str]:
    """
    Compute CSS Grid named row lines from talk start/end boundaries.

    Returns ``(sorted_boundaries, css_grid_template_rows)`` where each boundary becomes a named grid
    line like ``[t-0930]``. The height between two consecutive boundaries is proportional to the
    time gap (2 px per minute, minimum 20 px).
    """
    boundaries: set[datetime] = set()
    for t in talks:
        boundaries.add(t.start_time)
        boundaries.add(t.start_time + t.duration)

    sorted_bounds = sorted(boundaries)
    if len(sorted_bounds) < 2:  # noqa: PLR2004
        return sorted_bounds, ""

    px_per_min = 2
    min_px = 20

    parts: list[str] = []
    for i, bound in enumerate(sorted_bounds):
        local = timezone.localtime(bound)
        name = f"t-{local.strftime('%H%M')}"
        if i < len(sorted_bounds) - 1:
            gap_minutes = (sorted_bounds[i + 1] - bound) / timedelta(minutes=1)
            height = max(int(gap_minutes * px_per_min), min_px)
            parts.append(f"[{name}] minmax({height}px, auto)")
        else:
            parts.append(f"[{name}]")

    return sorted_bounds, " ".join(parts)


def _slice_name(dt: datetime) -> str:
    """Return the CSS grid line name for a datetime, e.g. ``t-0930``."""
    local = timezone.localtime(dt)
    return f"t-{local.strftime('%H%M')}"


def _get_schedule_dates(user: CustomUser, event_id: int | None = None) -> list[date]:
    """Return available schedule dates, filtered by user event access and optional event."""
    # Always scope to the user's accessible events first, then optionally narrow to one event.
    # Skipping `accessible_to` when ``event_id`` is set would let a user request any event's
    # schedule by passing ``?event=<id>`` in the URL.
    talks_qs = Talk.objects.exclude(start_time__year=FAR_FUTURE.year).accessible_to(user)
    if event_id:
        talks_qs = talks_qs.filter(event_id=event_id)
    date_qs = (
        talks_qs.annotate(date=TruncDate("start_time"))
        .values_list("date", flat=True)
        .distinct()
        .order_by("date")
    )
    return list(date_qs)


def _build_schedule_data(
    selected_date: date,
    user: CustomUser,
    event_id: int | None = None,
) -> tuple[list[Talk], list[Room], list[dict[str, Any]], str, list[dict[str, str]]]:
    """
    Build the CSS Grid schedule data for a given date.

    Returns ``(talks, rooms, schedule_items, grid_template_rows, time_labels)``.
    """
    talks_qs = (
        Talk.objects.filter(start_time__date=selected_date)
        .exclude(start_time__year=FAR_FUTURE.year)
        .select_related("room")
        .prefetch_related("speakers")
        .defer("description", "abstract")
        .accessible_to(user)
        .order_by("start_time", "room__name")
    )
    if event_id:
        talks_qs = talks_qs.filter(event_id=event_id)
    talks = list(talks_qs)
    # Cache streamings so per-talk get_video_link / get_transcription_url / has_active_streaming
    # in the template do not fan out to one Streaming query per row.
    prefetch_streamings(talks)

    # Unique rooms ordered by name
    room_ids_seen: set[int] = set()
    rooms_list: list[Room] = []
    for t in talks:
        rid = t.room_id  # type: ignore[attr-defined]
        if t.room and rid is not None and rid not in room_ids_seen:
            room_ids_seen.add(rid)
            rooms_list.append(t.room)
    rooms = sorted(rooms_list, key=lambda r: r.name)

    # Room → CSS grid column (col 1 = time label, rooms start at col 2)
    room_col: dict[int, int] = {r.id: idx + 2 for idx, r in enumerate(rooms)}  # type: ignore[attr-defined]

    # CSS Grid slices
    sorted_bounds, grid_template_rows = _build_grid_slices(talks)

    # Build schedule items with grid-area CSS
    schedule_items: list[dict[str, Any]] = []
    for t in talks:
        if not t.room:
            continue
        row_start = _slice_name(t.start_time)
        row_end = _slice_name(t.start_time + t.duration)
        rid = t.room_id  # type: ignore[attr-defined]
        col = room_col.get(rid, 2) if rid is not None else 2
        duration_min = int(t.duration.total_seconds() // 60)
        schedule_items.append(
            {
                "talk": t,
                "grid_area": f"{row_start} / {col} / {row_end}",
                "duration_min": duration_min,
            },
        )

    # Time labels for the first column
    time_labels: list[dict[str, str]] = []
    seen_labels: set[str] = set()
    for bound in sorted_bounds[:-1]:  # skip the last boundary (end-only)
        name = _slice_name(bound)
        if name not in seen_labels:
            seen_labels.add(name)
            local = timezone.localtime(bound)
            time_labels.append({"name": name, "display": local.strftime("%H:%M")})

    return talks, rooms, schedule_items, grid_template_rows, time_labels


def _talk_matches_filters(
    talk: Talk,
    filters: dict[str, str],
    saved_talk_ids: set[int],
) -> bool:
    """Return True if a talk matches all active filters."""
    if filters.get("saved") == "1" and talk.pk not in saved_talk_ids:
        return False
    filter_track = filters.get("track", "")
    if filter_track and talk.track != filter_track:
        return False
    filter_type = filters.get("presentation_type", "")
    if filter_type and talk.presentation_type != filter_type:
        return False
    search_query = filters.get("q", "")
    if search_query:
        q_lower = search_query.lower()
        if q_lower not in talk.title.lower() and q_lower not in talk.speaker_names.lower():
            return False
    return True


def _apply_schedule_filters(
    schedule_items: list[dict[str, Any]],
    filters: dict[str, str],
    saved_talk_ids: set[int],
) -> list[dict[str, Any]]:
    """Filter schedule items by search text, saved-only, track, and type."""
    if not any(filters.get(k) for k in ("q", "saved", "track", "presentation_type")):
        return schedule_items
    return [
        item
        for item in schedule_items
        if _talk_matches_filters(item["talk"], filters, saved_talk_ids)
    ]


def _resolve_selected_event_id(request: HttpRequest) -> int | None:
    """Return the event id to filter the schedule by, or None for cross-event view."""
    event_param = request.GET.get("event", "")
    if event_param:
        return int(event_param) if event_param.isdigit() else None
    default_event = resolve_default_event(request)
    return default_event.pk if default_event else None  # type: ignore[return-value]


def _resolve_selected_date(
    request: HttpRequest,
    available_dates: list[date],
) -> date | None:
    """Pick the best-available schedule date: user's ?date, today, or the first one."""
    selected_date = _parse_schedule_date(request.GET.get("date"))
    if selected_date in available_dates:
        return selected_date
    today = timezone.localdate()
    if today in available_dates:
        return today
    return available_dates[0] if available_dates else None


@require_safe
def schedule_view(request: HttpRequest) -> HttpResponse:
    """
    Render a Pretalx-style CSS Grid schedule.

    Each talk is positioned using CSS Grid named row lines so that overlapping talks in different
    rooms appear side-by-side and card heights are proportional to duration.
    """
    user = cast("CustomUser", request.user)

    selected_event_id = _resolve_selected_event_id(request)
    available_events = user.visible_events()

    available_dates = _get_schedule_dates(user, event_id=selected_event_id)
    selected_date = _resolve_selected_date(request, available_dates)

    # Build grid data ---------------------------------------------------------
    talks: list[Talk] = []
    rooms: list[Room] = []
    schedule_items: list[dict[str, Any]] = []
    grid_template_rows = ""
    time_labels: list[dict[str, str]] = []

    if selected_date:
        talks, rooms, schedule_items, grid_template_rows, time_labels = _build_schedule_data(
            selected_date,
            user,
            event_id=selected_event_id,
        )

    # Saved talk IDs for bookmark icons
    saved_talk_ids: set[int] = set()
    if request.user.is_authenticated:
        saved_talk_ids = SavedTalk.talk_ids_for(cast("CustomUser", request.user))

    # Filters -----------------------------------------------------------------
    search_query = request.GET.get("q", "").strip()
    filter_saved = request.GET.get("saved", "")
    filter_track = request.GET.get("track", "")
    filter_type = request.GET.get("presentation_type", "")
    schedule_filters = {
        "q": search_query,
        "saved": filter_saved,
        "track": filter_track,
        "presentation_type": filter_type,
    }
    schedule_items = _apply_schedule_filters(
        schedule_items,
        schedule_filters,
        saved_talk_ids,
    )

    # Track & type options for filter dropdowns
    all_tracks = sorted({t.track for t in talks if t.track})
    existing_types = sorted({t.presentation_type for t in talks if t.presentation_type})
    presentation_types = [(ptype, Talk.PresentationType(ptype).label) for ptype in existing_types]

    years = {d.year for d in available_dates}
    has_multiple_years = len(years) > 1

    context = {
        "available_dates": available_dates,
        "selected_date": selected_date,
        "has_multiple_years": has_multiple_years,
        "rooms": rooms,
        "has_schedule": bool(talks),
        "schedule_items": schedule_items,
        "grid_template_rows": grid_template_rows,
        "time_labels": time_labels,
        "talks": talks,
        "saved_talk_ids": saved_talk_ids,
        "search_query": search_query,
        "filter_saved": filter_saved,
        "tracks": all_tracks,
        "presentation_types": presentation_types,
        "selected_track": filter_track,
        "selected_type": filter_type,
        "events": available_events,
        "selected_event": str(selected_event_id) if selected_event_id else "",
    }
    return render(request, "talks/schedule.html", context)
