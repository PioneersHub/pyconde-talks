"""
Views for managing and displaying Talk objects.

This module provides class-based and function-based views for handling Talk-related operations,
including listing, detail views, and statistics.
"""

from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING, Any, cast

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import IntegrityError
from django.db.models import Avg, Count, Q
from django.db.models.functions import TruncDate
from django.http import Http404, HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.cache import cache_page
from django.views.decorators.http import require_POST
from django.views.generic import DetailView, ListView

from .models import (
    COMMENT_MAX_LENGTH,
    MAX_RATING_SCORE,
    MIN_RATING_SCORE,
    Rating,
    Room,
    SavedTalk,
    Talk,
)
from .utils import get_talk_by_id_or_pretalx


if TYPE_CHECKING:
    from django.db.models.query import QuerySet

    from users.models import CustomUser


class TalkDetailView(LoginRequiredMixin, DetailView[Talk]):
    """
    Display detailed information about a specific Talk.

    Requires user authentication to access the view.
    """

    model = Talk
    template_name = "talks/talk_detail.html"
    context_object_name = "talk"

    def get_queryset(self) -> QuerySet[Talk]:
        """Optimize query with related data."""
        qs = Talk.objects.select_related("room").prefetch_related("speakers")
        # Restrict to talks for events the user has access to
        user = cast("CustomUser", self.request.user)
        if not user.is_superuser:
            qs = qs.filter(
                Q(event__isnull=True) | Q(event__in=user.events.all()),
            )
        return qs

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        """Enhance context with rating statistics and user's existing rating."""
        context = super().get_context_data(**kwargs)
        talk = self.object

        # Aggregate rating stats in a single query
        stats = Rating.objects.filter(talk=talk).aggregate(
            avg=Avg("score"),
            count=Count("id"),
        )
        context["rating_count"] = stats["count"]
        context["average_rating"] = stats["avg"]

        # Get user's existing rating if authenticated
        if self.request.user.is_authenticated:
            context["user_rating"] = Rating.objects.filter(
                talk=talk,
                user=self.request.user,
            ).first()
            context["is_saved"] = SavedTalk.objects.filter(
                talk=talk,
                user=self.request.user,
            ).exists()

        return context


class TalkListView(LoginRequiredMixin, ListView[Talk]):
    """
    Display a list of Talk objects with filtering capabilities.

    Supports filtering by room and date, and provides context for filter options.
    Requires user authentication to access the view.
    """

    model: type[Talk] = Talk
    template_name = "talks/talk_list.html"
    context_object_name = "talks"

    def get_template_names(self) -> list[str]:
        """
        Determine which template to use.

        Return a partial fragment for HTMX requests.
        """
        if self.request.headers.get("HX-Request"):
            return ["talks/talk_list.html#talk-list"]
        return [self.template_name]

    def get_queryset(self) -> QuerySet[Talk]:
        """Get the list of talks filtered by room, date, track, presentation type, and query."""
        # Defer large text fields not needed in list view to reduce memory usage
        queryset: QuerySet[Talk] = (
            Talk.objects.select_related("room")
            .prefetch_related("speakers")
            .defer("description", "abstract")
        )

        # Restrict to talks for events the user has access to
        user = cast("CustomUser", self.request.user)
        if not user.is_superuser:
            queryset = queryset.filter(
                Q(event__isnull=True) | Q(event__in=user.events.all()),
            )

        queryset = self._apply_list_filters(queryset)
        queryset = _apply_search_filter(queryset, self.request)

        # Annotate with rating statistics for list display
        queryset = queryset.annotate(
            average_rating=Avg("ratings__score"),
            rating_count=Count("ratings"),
        )

        return queryset.order_by("start_time")

    def _apply_list_filters(self, queryset: QuerySet[Talk]) -> QuerySet[Talk]:
        """Apply room, date, track, presentation type, and saved filters from GET params."""
        filters: dict[str, str | None] = {
            "room_id": self.request.GET.get("room"),
            "start_time__date": self.request.GET.get("date"),
            "track": self.request.GET.get("track"),
            "presentation_type": self.request.GET.get("presentation_type"),
        }
        active = {k: v for k, v in filters.items() if v}
        if active:
            queryset = queryset.filter(**active)

        # Filter by saved talks
        if self.request.GET.get("saved") == "1":
            queryset = queryset.filter(
                saved_by__user=self.request.user,
            )

        return queryset

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        """Enhance the template context with filter options and selected values."""
        context = super().get_context_data(**kwargs)

        # Get unique rooms
        context["rooms"] = Room.objects.filter(talks__isnull=False).distinct().order_by("name")
        # Get unique days
        context["dates"] = (
            Talk.objects.annotate(date=TruncDate("start_time"))
            .values_list("date", flat=True)
            .distinct()
            .order_by("date")
        )

        # Check if there are multiple years
        years = {d.year for d in context["dates"]}
        context["has_multiple_years"] = len(years) > 1

        # Get unique tracks
        context["tracks"] = (
            Talk.objects.values_list("track", flat=True).distinct().order_by("track")
        )
        # Get presentation types
        existing_types = (
            Talk.objects.values_list("presentation_type", flat=True)
            .distinct()
            .order_by("presentation_type")
        )
        context["presentation_types"] = [
            (ptype, Talk.PresentationType(ptype).label) for ptype in existing_types
        ]

        # Selected values
        context["selected_room"] = self.request.GET.get("room", "")
        context["selected_date"] = self.request.GET.get("date", "")
        context["selected_track"] = self.request.GET.get("track", "")
        context["selected_type"] = self.request.GET.get("presentation_type", "")
        context["search_query"] = self.request.GET.get("q", "")
        context["search_in"] = self.request.GET.getlist("search_in") or ["all"]
        context["filter_saved"] = self.request.GET.get("saved", "")

        # Build a set of saved talk IDs for the current user
        if self.request.user.is_authenticated:
            context["saved_talk_ids"] = set(
                SavedTalk.objects.filter(user=self.request.user).values_list("talk_id", flat=True),
            )
        else:
            context["saved_talk_ids"] = set()

        return context


@login_required
@cache_page(60)  # Cache for 60 seconds to reduce database queries
def dashboard_stats(request: HttpRequest) -> HttpResponse:
    """Generate statistics for the dashboard."""
    current_time = timezone.now()

    # Optimize recorded_talks: only fetch fields needed for get_video_link()
    # Use select_related for room to avoid N+1 queries
    talks_for_video_check = Talk.objects.select_related("room").only(
        "id",
        "video_link",
        "start_time",
        "duration",
        "room",
        "room__id",
    )

    context = {
        "total_talks": Talk.objects.count(),
        "todays_talks": Talk.objects.filter(start_time__date=current_time.date()).count(),
        "recorded_talks": sum(1 for talk in talks_for_video_check if talk.get_video_link()),
    }
    return render(request, "talks/partials/dashboard_stats.html", context)


@login_required
@cache_page(30)  # Cache for 30 seconds - talks list changes infrequently
def upcoming_talks(request: HttpRequest) -> HttpResponse:
    """Display the next 8 upcoming talks."""
    current_time = timezone.now()
    talks = (
        Talk.objects.select_related("room")
        .prefetch_related("speakers")
        .filter(start_time__gt=current_time)
        .annotate(
            average_rating=Avg("ratings__score"),
            rating_count=Count("ratings"),
        )
        .order_by("start_time")[:8]
    )
    context = {"upcoming_talks": talks}
    return render(request, "talks/partials/upcoming_talks.html", context)


def talk_redirect_view(_: HttpRequest, talk_id: str) -> HttpResponse:
    """Get talk detail view by Talk ID or Pretalx ID."""
    talk = get_talk_by_id_or_pretalx(talk_id)
    if talk:
        return redirect("talk_detail", pk=talk.pk)
    msg = f"No talk found with ID or Pretalx ID: {talk_id}"
    raise Http404(msg)


def _rating_error_response(
    request: HttpRequest,
    talk_id: int,
    message: str,
    *,
    is_htmx: bool,
    status: int = 422,
) -> HttpResponse:
    """Return an error response for rating operations (HTMX fragment or redirect)."""
    if is_htmx:
        return HttpResponse(message, status=status)
    messages.error(request, message)
    return redirect("talk_detail", pk=talk_id)


def _render_rating_htmx_response(
    request: HttpRequest,
    talk: Talk,
    *,
    is_comment_save: bool,
) -> HttpResponse:
    """Render the rating widget and OOB title stars for an HTMX response."""
    stats = Rating.objects.filter(talk=talk).aggregate(
        avg=Avg("score"),
        count=Count("id"),
    )
    user_rating = Rating.objects.filter(talk=talk, user=request.user).first()
    context = {
        "talk": talk,
        "average_rating": stats["avg"],
        "rating_count": stats["count"],
        "user_rating": user_rating,
        "show_comment_form": not is_comment_save,
        # Preserve in-progress comment text on star clicks (not saved to DB)
        "draft_comment": request.POST.get("comment") if not is_comment_save else None,
    }
    widget_html = render(request, "talks/partials/rating_widget.html", context).content.decode()
    oob_html = render(
        request,
        "talks/partials/title_star_rating_oob.html",
        context,
    ).content.decode()
    return HttpResponse(widget_html + oob_html)


def _apply_search_filter(queryset: QuerySet[Talk], request: HttpRequest) -> QuerySet[Talk]:
    """Apply free-text search with scope filtering to the talk queryset."""
    query = (request.GET.get("q") or "").strip()
    if not query:
        return queryset

    raw_scopes = [s.strip() for s in request.GET.getlist("search_in") if s.strip()]
    scopes = set(raw_scopes or ["all"])
    if "all" in scopes or not scopes:
        scopes = {"title", "author", "description"}

    q_obj = Q()
    if "title" in scopes:
        q_obj |= Q(title__icontains=query)
    if "description" in scopes:
        q_obj |= Q(description__icontains=query) | Q(abstract__icontains=query)
    if "author" in scopes:
        q_obj |= Q(speakers__name__icontains=query)

    return queryset.filter(q_obj).distinct()


@login_required
@require_POST
def rate_talk(request: HttpRequest, talk_id: int) -> HttpResponse:
    """
    Handle talk rating submission.

    Users can rate a talk from 1 to 5 stars with an optional comment.
    If a rating already exists, it is updated.
    Returns a partial HTML fragment for HTMX requests or redirects otherwise.
    """
    talk = get_object_or_404(Talk, pk=talk_id)
    is_htmx = request.headers.get("HX-Request") == "true"
    is_comment_save = request.POST.get("save_comment") == "1"

    # Validate score
    try:
        score = int(request.POST.get("score"))  # type: ignore[arg-type]
    except (TypeError, ValueError):  # fmt: skip
        return _rating_error_response(request, talk_id, "Invalid rating value.", is_htmx=is_htmx)

    if score < MIN_RATING_SCORE or score > MAX_RATING_SCORE:
        return _rating_error_response(
            request,
            talk_id,
            f"Rating must be between {MIN_RATING_SCORE} and {MAX_RATING_SCORE} stars.",
            is_htmx=is_htmx,
        )

    # Build defaults: only update comment when explicitly saving it (HTMX star clicks skip comment)
    defaults: dict[str, Any] = {"score": score}
    if is_comment_save or not is_htmx:
        defaults["comment"] = request.POST.get("comment", "").strip()[:COMMENT_MAX_LENGTH]

    # Create or update rating
    try:
        _rating, created = Rating.objects.update_or_create(
            talk=talk,
            user=request.user,
            defaults=defaults,
        )
        if not is_htmx:
            msg = "Your rating has been submitted!" if created else "Your rating has been updated!"
            messages.success(request, msg)
    except IntegrityError:
        return _rating_error_response(
            request,
            talk_id,
            "Error submitting rating. Please try again.",
            is_htmx=is_htmx,
            status=500,
        )

    if is_htmx:
        return _render_rating_htmx_response(request, talk, is_comment_save=is_comment_save)

    return redirect("talk_detail", pk=talk_id)


@login_required
def get_talk_rating_stats(request: HttpRequest, talk_id: int) -> JsonResponse:
    """
    Return rating statistics for a talk as JSON.

    Returns average_rating, rating_count, and the current user's rating if it exists.
    """
    talk = get_object_or_404(Talk, pk=talk_id)

    stats = Rating.objects.filter(talk=talk).aggregate(
        average=Avg("score"),
        count=Count("id"),
    )

    user_rating = None
    if request.user.is_authenticated:
        rating = Rating.objects.filter(talk=talk, user=request.user).first()
        if rating:
            user_rating = {
                "score": rating.score,
                "comment": rating.comment,
            }

    return JsonResponse(
        {
            "average_rating": round(stats["average"], 1) if stats["average"] else None,
            "rating_count": stats["count"],
            "user_rating": user_rating,
        },
    )


@login_required
@require_POST
def toggle_save_talk(request: HttpRequest, talk_id: int) -> HttpResponse:
    """
    Toggle a talk's saved/bookmarked status for the current user.

    If the talk is already saved, it removes the saved status. Otherwise, it saves the talk.
    Returns an HTMX partial with the updated bookmark button.
    """
    talk = get_object_or_404(Talk, pk=talk_id)
    saved_talk, created = SavedTalk.objects.get_or_create(
        user=request.user,
        talk=talk,
    )

    if not created:
        saved_talk.delete()

    is_saved = created
    is_htmx = request.headers.get("HX-Request") == "true"

    if is_htmx:
        return render(
            request,
            "talks/partials/save_button.html",
            {"talk": talk, "is_saved": is_saved},
        )

    if is_saved:
        messages.success(request, "Talk saved!")
    else:
        messages.info(request, "Talk removed from saved.")
    return redirect("talk_detail", pk=talk_id)


# ---------------------------------------------------------------------------
# Schedule grid view
# ---------------------------------------------------------------------------


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

    Returns ``(sorted_boundaries, css_grid_template_rows)`` where each boundary
    becomes a named grid line like ``[t-0930]``.  The height between two
    consecutive boundaries is proportional to the time gap (2 px per minute,
    minimum 20 px).
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


def _get_schedule_dates(user: CustomUser) -> list[date]:
    """Return available schedule dates, filtered by user event access."""
    date_qs = (
        Talk.objects.exclude(start_time__year=2050)
        .annotate(date=TruncDate("start_time"))
        .values_list("date", flat=True)
        .distinct()
        .order_by("date")
    )
    if not user.is_superuser:
        date_qs = date_qs.filter(
            Q(event__isnull=True) | Q(event__in=user.events.all()),
        )
    return list(date_qs)


def _build_schedule_data(
    selected_date: date,
    user: CustomUser,
) -> tuple[list[Talk], list[Room], list[dict[str, Any]], str, list[dict[str, str]]]:
    """
    Build the CSS Grid schedule data for a given date.

    Returns ``(talks, rooms, schedule_items, grid_template_rows, time_labels)``.
    """
    talks_qs = (
        Talk.objects.filter(start_time__date=selected_date)
        .exclude(start_time__year=2050)
        .select_related("room")
        .prefetch_related("speakers")
        .defer("description", "abstract")
        .order_by("start_time", "room__name")
    )
    if not user.is_superuser:
        talks_qs = talks_qs.filter(
            Q(event__isnull=True) | Q(event__in=user.events.all()),
        )
    talks = list(talks_qs)

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


def _apply_schedule_filters(
    schedule_items: list[dict[str, Any]],
    search_query: str,
    filter_saved: str,
    saved_talk_ids: set[int],
) -> list[dict[str, Any]]:
    """Filter schedule items by search text and saved-only flag."""
    if not search_query and not filter_saved:
        return schedule_items
    filtered: list[dict[str, Any]] = []
    for item in schedule_items:
        talk: Talk = item["talk"]
        if filter_saved == "1" and talk.pk not in saved_talk_ids:
            continue
        if search_query:
            q_lower = search_query.lower()
            if q_lower not in talk.title.lower() and q_lower not in talk.speaker_names.lower():
                continue
        filtered.append(item)
    return filtered


@login_required
def schedule_view(request: HttpRequest) -> HttpResponse:
    """
    Render a Pretalx-style CSS Grid schedule.

    Each talk is positioned using CSS Grid named row lines so that
    overlapping talks in different rooms appear side-by-side and card
    heights are proportional to duration.
    """
    user = cast("CustomUser", request.user)

    available_dates = _get_schedule_dates(user)

    # Resolve selected date ---------------------------------------------------
    selected_date = _parse_schedule_date(request.GET.get("date"))
    if selected_date not in available_dates:
        selected_date = available_dates[0] if available_dates else None

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
        )

    # Saved talk IDs for bookmark icons
    saved_talk_ids: set[int] = set()
    if request.user.is_authenticated:
        saved_talk_ids = set(
            SavedTalk.objects.filter(user=request.user).values_list("talk_id", flat=True),
        )

    # Filters -----------------------------------------------------------------
    search_query = request.GET.get("q", "").strip()
    filter_saved = request.GET.get("saved", "")
    schedule_items = _apply_schedule_filters(
        schedule_items,
        search_query,
        filter_saved,
        saved_talk_ids,
    )

    years = {d.year for d in available_dates}
    has_multiple_years = len(years) > 1

    context = {
        "available_dates": available_dates,
        "selected_date": selected_date,
        "has_multiple_years": has_multiple_years,
        "rooms": rooms,
        "schedule_items": schedule_items,
        "grid_template_rows": grid_template_rows,
        "time_labels": time_labels,
        "talks": talks,
        "saved_talk_ids": saved_talk_ids,
        "search_query": search_query,
        "filter_saved": filter_saved,
    }
    return render(request, "talks/schedule.html", context)
