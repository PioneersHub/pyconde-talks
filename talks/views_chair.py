"""
Session-chair views (moderator-only).

Lets a moderator volunteer (or step down) as the session chair for a block of adjacent talks in
the same room and renders a day grid (times on the left, rooms across the top) showing who is
chairing each session.
"""

from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING, Any, cast

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.db.models import Q
from django.db.models.functions import TruncDate
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST, require_safe

from events.session import resolve_default_event

from .grid_utils import build_grid_slices, build_time_labels, grid_line_name
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


def is_admin(user: CustomUser) -> bool:
    """Return True for superusers, who may assign any moderator as chair (not just themselves)."""
    return bool(getattr(user, "is_superuser", False))


def _user_label(user: CustomUser) -> str:
    """Return the most human-readable name for a user: display name, full name, or email."""
    return str(user.display_name.strip() or user.get_full_name().strip() or user.email)  # type: ignore[attr-defined]


def _talks_overlap(a: Talk, b: Talk) -> bool:
    """Return True if talk a and talk b overlap in time (partial overlap counts)."""
    if not a.start_time or not b.start_time:
        return False
    return a.start_time < b.start_time + b.duration and b.start_time < a.start_time + a.duration


def _find_chair_conflicts(target_user: CustomUser, block: list[Talk]) -> list[Talk]:
    """
    Return talks already chaired by target_user that would overlap in time with the block.

    Any overlap (even partial) is a conflict. Talks already in the block are excluded - they will
    be reassigned and therefore cannot conflict with themselves.
    """
    dates = {t.start_time.date() for t in block if t.start_time}
    if not dates:
        return []
    block_pks = {t.pk for t in block}
    existing = list(
        Talk.objects.filter(session_chair=target_user, start_time__date__in=dates)
        .exclude(pk__in=block_pks)
        .exclude(start_time__year=FAR_FUTURE.year)
        .select_related("room")
    )
    seen: set[int] = set()
    conflicts: list[Talk] = []
    for block_talk in block:
        for chaired in existing:
            if chaired.pk not in seen and _talks_overlap(block_talk, chaired):
                seen.add(chaired.pk)
                conflicts.append(chaired)
    return conflicts


def _get_available_chairs(user: CustomUser, event_id: int | None) -> list[CustomUser]:
    """
    Return users eligible to chair sessions for the given event scope.

    Superusers are always included (they have implicit access to all events).
    Regular staff must be members of the specific event - or, when no event is
    selected, members of any event visible to the requesting admin.
    """
    UserModel = cast("type[CustomUser]", get_user_model())  # noqa: N806  # NOSONAR(S117)
    qs = UserModel.objects.filter(Q(is_staff=True) | Q(is_superuser=True))
    if event_id:
        qs = qs.filter(Q(is_superuser=True) | Q(events__id=event_id))
    else:
        accessible = user.visible_events()  # type: ignore[attr-defined]
        qs = qs.filter(Q(is_superuser=True) | Q(events__in=accessible))
    return list(qs.order_by("display_name", "email").distinct())


def _parse_chair_date(date_str: str | None) -> date | None:
    """Parse a YYYY-MM-DD string into a date, returning None on failure."""
    if not date_str:
        return None
    try:
        return datetime.strptime(date_str, "%Y-%m-%d").date()  # noqa: DTZ007
    except ValueError:
        return None


_MAX_CONFLICT_TITLES = 3


def _conflict_message(label: str, conflicts: list[Talk]) -> str:
    parts = []
    for c in conflicts[:_MAX_CONFLICT_TITLES]:
        room = c.room.name if c.room else "?"
        parts.append(f"{c.title} ({room})")
    suffix = " and more" if len(conflicts) > _MAX_CONFLICT_TITLES else ""
    return f"{label} already chairs a session at the same time: {', '.join(parts)}{suffix}"


def _admin_assign(talk: Talk, raw_id: str) -> str | None:
    """
    Execute the admin assignment path: assign a specific user or clear the talk.

    Returns an error string when the assignment is blocked by a time conflict, or None on success.
    """
    if not (raw_id and raw_id.isdigit()):
        talk.session_chair = None
        talk.save(update_fields=["session_chair", "updated_at"])
        return None
    UserModel = cast("type[CustomUser]", get_user_model())  # noqa: N806  # NOSONAR(S117)
    target = get_object_or_404(UserModel, pk=int(raw_id))
    if not is_moderator(target):
        raise PermissionDenied
    conflicts = _find_chair_conflicts(target, [talk])
    if conflicts:
        return _conflict_message(_user_label(target), conflicts)
    talk.session_chair = target
    talk.save(update_fields=["session_chair", "updated_at"])
    return None


def _mod_toggle(user: CustomUser, talk: Talk) -> str | None:
    """
    Execute the moderator self-toggle: claim or release a single talk.

    Returns an error string when claiming is blocked by a time conflict, or None on success.
    """
    if talk.session_chair_id not in (None, user.pk):
        return None  # Someone else chairs this talk; silently do nothing.
    new_chair: CustomUser | None = None if talk.session_chair_id == user.pk else user
    if new_chair is not None:
        conflicts = _find_chair_conflicts(new_chair, [talk])
        if conflicts:
            return _conflict_message("You", conflicts)
    talk.session_chair = new_chair
    talk.save(update_fields=["session_chair", "updated_at"])
    return None


def _chair_redirect(request: HttpRequest, talk: Talk, chair_error: str | None) -> HttpResponse:
    """Build the non-HTMX redirect response, attaching any error via the messages framework."""
    if chair_error:
        messages.error(request, chair_error)
    selected_date = talk.start_time.date() if talk.start_time else None
    selected_event = request.POST.get("event", "")
    url = reverse("chair_grid")
    params = []
    if selected_date:
        params.append(f"date={selected_date.isoformat()}")
    if selected_event:
        params.append(f"event={selected_event}")
    if params:
        url = f"{url}?{'&'.join(params)}"
    return redirect(url)


@require_POST
def toggle_session_chair(request: HttpRequest, talk_id: int) -> HttpResponse:
    """
    Claim or release the session chair for a single talk.

    Moderators may claim an unassigned talk or release one they already chair.
    Admins (superusers) may assign any moderator to any talk, or clear it.
    The same person cannot chair two overlapping sessions.
    """
    user = cast("CustomUser", request.user)
    _require_moderator(user)

    talk = get_object_or_404(Talk.objects.accessible_to(user), pk=talk_id)

    if "chair_user_id" in request.POST:
        if not is_admin(user):
            raise PermissionDenied
        chair_error = _admin_assign(talk, request.POST.get("chair_user_id", ""))
    else:
        chair_error = _mod_toggle(user, talk)

    if is_htmx_request(request):
        selected_event = request.POST.get("event", "")
        event_id = int(selected_event) if selected_event.isdigit() else None
        selected_date = talk.start_time.date() if talk.start_time else None
        context = _grid_context(user, event_id, selected_date)
        context["chair_error"] = chair_error
        return render(request, "talks/partials/chair_grid_table.html", context)

    return _chair_redirect(request, talk, chair_error)


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
) -> tuple[list[Room], list[dict[str, Any]], str, list[dict[str, str]]]:
    """
    Build chair grid data using the same CSS Grid layout as the schedule view.

    Returns ``(rooms, chair_items, grid_template_rows, time_labels)``.  Each chair item
    carries the talk, its CSS ``grid_area``, and the block id used for hover highlighting.
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

    # Unique rooms ordered by name.
    room_ids_seen: set[int] = set()
    rooms_list: list[Room] = []
    for t in talks:
        if t.room and t.room_id not in room_ids_seen:
            room_ids_seen.add(t.room_id)  # type: ignore[arg-type]
            rooms_list.append(t.room)
    rooms = sorted(rooms_list, key=lambda r: r.name)
    room_col: dict[int, int] = {r.id: idx + 2 for idx, r in enumerate(rooms)}  # type: ignore[attr-defined]

    # Tag each talk with its contiguous block id for hover highlighting.
    _assign_block_ids(talks)

    # CSS Grid row template from talk time boundaries (shared with schedule view).
    sorted_bounds, grid_template_rows = build_grid_slices(talks)

    # One grid item per talk, positioned by CSS grid-area.
    chair_items: list[dict[str, Any]] = []
    for t in talks:
        if not t.room or not t.room_id:
            continue
        col = room_col.get(t.room_id, 2)  # type: ignore[arg-type]
        row_start = grid_line_name(t.start_time)
        row_end = grid_line_name(t.start_time + t.duration)
        chair_items.append(
            {
                "talk": t,
                "grid_area": f"{row_start} / {col} / {row_end}",
                "duration_min": int(t.duration.total_seconds() // 60),
            }
        )

    return rooms, chair_items, grid_template_rows, build_time_labels(sorted_bounds)


def _grid_context(
    user: CustomUser,
    selected_event_id: int | None,
    selected_date: date | None,
) -> dict[str, Any]:
    """Build the template context shared by the full grid page and the HTMX table fragment."""
    available_dates = _chair_dates(user, selected_event_id)
    rooms: list[Room] = []
    chair_items: list[dict[str, Any]] = []
    grid_template_rows = ""
    time_labels: list[dict[str, str]] = []
    if selected_date:
        rooms, chair_items, grid_template_rows, time_labels = _build_chair_grid(
            selected_date,
            user,
            selected_event_id,
        )

    years = {d.year for d in available_dates}
    admin = is_admin(user)
    if admin:
        all_chairs = _get_available_chairs(user, selected_event_id)
        # Current user first so self-assignment is always one click, then alphabetical.
        me = next((c for c in all_chairs if c.pk == user.pk), None)
        others = [c for c in all_chairs if c.pk != user.pk]
        available_chairs: list[CustomUser] = ([me] if me else []) + others
    else:
        available_chairs = []
    return {
        "available_dates": available_dates,
        "selected_date": selected_date,
        "has_multiple_years": len(years) > 1,
        "rooms": rooms,
        "chair_items": chair_items,
        "grid_template_rows": grid_template_rows,
        "time_labels": time_labels,
        "has_grid": bool(chair_items),
        "events": user.visible_events(),
        "selected_event": str(selected_event_id) if selected_event_id else "",
        "current_user_id": user.pk,
        "is_admin": admin,
        "available_chairs": available_chairs,
        "chair_error": None,
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
