"""
Views for managing and displaying Talk objects.

Core browsing: list, detail, dashboard, upcoming, and ID-or-pretalx redirect. Rating endpoints
live in ``talks.views_rating`` and the bookmark toggle in ``talks.views_saved``.
"""

from datetime import timedelta
from typing import TYPE_CHECKING, Any, cast

from django.db.models import Count, Q
from django.db.models.functions import TruncDate
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.cache import cache_page
from django.views.decorators.http import require_safe
from django.views.decorators.vary import vary_on_cookie
from django.views.generic import DetailView, ListView

from events.models import Event
from events.session import resolve_default_event

from .models import Rating, Room, SavedTalk, Talk, TalkQuerySet, prefetch_streamings
from .utils import get_talk_by_id_or_pretalx, is_htmx_request, parse_iso_date
from .views_qa import is_moderator


if TYPE_CHECKING:
    from django.db.models.query import QuerySet

    from users.models import CustomUser


def _can_see_rating_summary(user: Any, event: Event | None) -> bool:
    """Return True if the user may see aggregate rating stats for this event."""
    if is_moderator(user):
        return True
    if event is None:
        # No event context (e.g. "all events" filter): default to hidden so per-event
        # show_rating_summary=False is not silently bypassed.
        return False
    return event.show_rating_summary


class TalkDetailView(DetailView[Talk]):
    """
    Display detailed information about a specific Talk.

    Requires user authentication to access the view.
    """

    model = Talk
    template_name = "talks/talk_detail.html"
    context_object_name = "talk"

    def get_queryset(self) -> QuerySet[Talk]:
        """Optimize query with related data."""
        user = cast("CustomUser", self.request.user)
        # select_related("event"): the detail template's get_image_url and the rating-summary
        # check both dereference talk.event, which would otherwise be a separate query per page.
        return (
            Talk.objects.select_related("room", "event")
            .prefetch_related("speakers")
            .accessible_to(user)
        )

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        """Enhance context with rating statistics and user's existing rating."""
        context = super().get_context_data(**kwargs)
        talk = self.object

        stats = talk.get_rating_stats()
        show_summary = _can_see_rating_summary(self.request.user, talk.event)

        if show_summary:
            context["rating_count"] = stats.total
            context["average_rating"] = stats.average
        else:
            context["rating_count"] = 0
            context["average_rating"] = None
        context["show_rating_summary"] = show_summary

        # Only moderators see the assigned session chair on the detail page.
        context["user_can_moderate"] = is_moderator(self.request.user)

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

        # Expose rating comments to superusers only. The field is otherwise kept private
        # (hence the admin-only copy in the Rating model's help_text), but superusers need to
        # read attendee feedback from the frontend in addition to the admin site.
        if getattr(self.request.user, "is_superuser", False):
            context["rating_comments"] = list(
                Rating.objects.filter(talk=talk)
                .exclude(comment="")
                .select_related("user")
                .order_by("-created_at"),
            )

        return context


class TalkListView(ListView[Talk]):
    """
    Display a list of Talk objects with filtering capabilities.

    Supports filtering by room and date, and provides context for filter options.
    Requires user authentication to access the view.
    """

    model = Talk
    template_name = "talks/talk_list.html"
    context_object_name = "talks"

    def get_template_names(self) -> list[str]:
        """
        Determine which template to use.

        Return a partial fragment for HTMX requests.
        """
        if is_htmx_request(self.request):
            return ["talks/talk_list.html#talk-list"]
        return [cast("str", self.template_name)]  # type: ignore[redundant-cast]

    def get_queryset(self) -> QuerySet[Talk]:
        """Get the list of talks filtered by room, date, track, presentation type, and query."""
        queryset = self._base_queryset()

        queryset = self._apply_event_filter(queryset)
        queryset = self._apply_list_filters(queryset)
        queryset = _apply_search_filter(queryset, self.request)

        return queryset.with_rating_stats().order_by("start_time")

    def _base_queryset(self) -> TalkQuerySet:
        """Return talks scoped to user access with list-view optimizations."""
        # Defer large text fields not needed in list view to reduce memory usage
        user = cast("CustomUser", self.request.user)
        return (
            Talk.objects.select_related("room")
            .prefetch_related("speakers")
            .defer("description", "abstract")
            .accessible_to(user)
        )

    def _filter_options_queryset(self) -> TalkQuerySet:
        """Return talks used to build filter options for the selected event/search scope."""
        queryset = self._base_queryset()
        queryset = self._apply_event_filter(queryset)
        return _apply_search_filter(queryset, self.request)

    def _apply_event_filter(self, queryset: TalkQuerySet) -> TalkQuerySet:
        """Filter talks by event. Defaults to the current event from session/settings."""
        event_id = self.request.GET.get("event", "")
        if event_id == "all":
            return queryset
        if event_id.isdigit():
            return queryset.filter(event_id=event_id)
        # No usable selection: empty, or garbage like ``?event=abc``. Fall through to the
        # resolved current event. The ``isdigit`` guard also avoids ``ValueError`` from
        # ``filter(event_id=...)`` on non-numeric input.
        default_event = resolve_default_event(self.request)
        if default_event:
            queryset = queryset.filter(event=default_event)
        return queryset

    def _apply_list_filters(self, queryset: TalkQuerySet) -> TalkQuerySet:
        """
        Apply room, date, track, type, and saved filters from GET params.

        Each value is validated against the event-scoped ``queryset`` to discard stale params left
        over from a previous event switch.
        All valid params are then applied together in a single ``.filter()`` so that intersecting
        criteria (e.g. Room A + April 6) correctly produce an empty result when no talks match both.
        """
        active: dict[str, str] = {}

        room_id = self.request.GET.get("room")
        # Guard isdigit so a non-numeric ?room= doesn't raise ValueError from the filter.
        # A valid id from another event is harmless: the queryset is already event-scoped,
        # so .exists() is False and the stale param is dropped.
        if room_id and room_id.isdigit() and queryset.filter(room_id=room_id).exists():
            active["room_id"] = room_id

        date_value = self.request.GET.get("date")
        if (
            date_value
            and parse_iso_date(date_value)
            and queryset.filter(start_time__date=date_value).exists()
        ):
            active["start_time__date"] = date_value

        track = self.request.GET.get("track")
        if track and queryset.filter(track=track).exists():
            active["track"] = track

        presentation_type = self.request.GET.get("presentation_type")
        if presentation_type and queryset.filter(presentation_type=presentation_type).exists():
            active["presentation_type"] = presentation_type

        if active:
            queryset = queryset.filter(**active)

        # Filter by saved talks
        if self.request.GET.get("saved") == "1":
            queryset = queryset.filter(
                saved_by__user=self.request.user,
            )

        # Filter by talk status
        status = self.request.GET.get("status", "")
        if status:
            queryset = _apply_status_filter(queryset, status)

        return queryset

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        """Enhance the template context with filter options and selected values."""
        context = super().get_context_data(**kwargs)

        # Event filter options & selection
        user = cast("CustomUser", self.request.user)
        context["events"] = user.visible_events()

        event_param = self.request.GET.get("event", "")
        if event_param:
            context["selected_event"] = event_param
        else:
            default_event = resolve_default_event(self.request)
            context["selected_event"] = str(default_event.pk) if default_event else ""

        # Scope filter options to selected event/search, not current room/date/track/type
        talk_qs = self._filter_options_queryset()

        # Get unique rooms
        context["rooms"] = Room.objects.filter(talks__in=talk_qs).distinct().order_by("name")
        # Get unique days
        context["dates"] = (
            talk_qs.annotate(date=TruncDate("start_time"))
            .values_list("date", flat=True)
            .distinct()
            .order_by("date")
        )

        # Check if there are multiple years
        years = {d.year for d in context["dates"]}
        context["has_multiple_years"] = len(years) > 1

        # Get unique tracks
        context["tracks"] = talk_qs.values_list("track", flat=True).distinct().order_by("track")
        # Get presentation types
        existing_types = (
            talk_qs.values_list("presentation_type", flat=True)
            .distinct()
            .order_by("presentation_type")
        )
        context["presentation_types"] = [
            (ptype, Talk.PresentationType(ptype).label) for ptype in existing_types
        ]

        # Selected values - clear stale selections that no longer match the event
        selected_room = self.request.GET.get("room", "")
        room_ids = {str(room.pk) for room in context["rooms"]}
        context["selected_room"] = selected_room if selected_room in room_ids else ""

        selected_date = self.request.GET.get("date", "")
        valid_dates = {d.strftime("%Y-%m-%d") for d in context["dates"]}
        context["selected_date"] = selected_date if selected_date in valid_dates else ""

        selected_track = self.request.GET.get("track", "")
        valid_tracks = set(context["tracks"])
        context["selected_track"] = selected_track if selected_track in valid_tracks else ""

        selected_type = self.request.GET.get("presentation_type", "")
        valid_types = {t[0] for t in context["presentation_types"]}
        context["selected_type"] = selected_type if selected_type in valid_types else ""
        context["search_query"] = self.request.GET.get("q", "")
        context["search_in"] = self.request.GET.getlist("search_in") or ["all"]
        context["filter_saved"] = self.request.GET.get("saved", "")

        # Status filter
        context["selected_status"] = self.request.GET.get("status", "")
        context["status_choices"] = [
            ("current", "Happening Now"),
            ("upcoming", "Upcoming"),
            ("completed", "Completed"),
        ]

        # Build a set of saved talk IDs for the current user. LoginRequiredMiddleware ensures
        # this view only runs for authenticated users.
        context["saved_talk_ids"] = SavedTalk.talk_ids_for(
            cast("CustomUser", self.request.user),
        )

        # Determine whether rating summaries are visible to this user
        selected_event = self._resolve_selected_event()
        context["show_rating_summary"] = _can_see_rating_summary(
            self.request.user,
            selected_event,
        )
        context["is_htmx_request"] = is_htmx_request(self.request)

        # Cache streamings for the rows actually rendered to dodge per-row queries
        # from ``talk.get_video_link`` / ``talk.get_transcription_url`` in the template.
        # ``object_list`` here is already a list (a paginator Page or ListView's queryset).
        page_obj = context.get("page_obj")
        rendered_talks = list(page_obj.object_list if page_obj else context.get("talks", []))
        prefetch_streamings(rendered_talks)

        return context

    def _resolve_selected_event(self) -> Event | None:
        """Return the currently filtered event, or the default event."""
        event_param = self.request.GET.get("event", "")
        if event_param == "all":
            return None
        if event_param.isdigit():
            return Event.objects.filter(pk=event_param, is_active=True).first()
        # Empty or garbage: mirror ``_apply_event_filter`` and fall back to the default
        # event so the selected-event indicator matches the talks actually shown.
        return resolve_default_event(self.request)


@require_safe
@cache_page(60)  # Cache for 60 seconds to reduce database queries
@vary_on_cookie
def dashboard_stats(request: HttpRequest) -> HttpResponse:
    """Generate per-event statistics for the dashboard, respecting user access."""
    user = cast("CustomUser", request.user)
    current_date = timezone.now().date()

    # Determine which events the user may see. Materialize once so we iterate the
    # same in-memory list later instead of re-querying for the row data.
    events = list(user.visible_events())
    event_ids = [event.id for event in events]  # type: ignore[attr-defined]

    # Fetch only the fields needed for get_video_link() - scoped to user events.
    # ``with_streamings`` batch-loads the streaming cache to avoid an N+1 in the
    # ``get_video_link`` loop below (each unrecorded talk would otherwise re-query).
    talks_for_video = (
        Talk.objects.filter(event_id__in=event_ids)
        .select_related("room")
        .only("id", "video_link", "start_time", "duration", "room", "room__id", "event")
        .with_streamings()
    )
    recorded_by_event: dict[int | None, int] = {}
    for talk in talks_for_video:
        if talk.get_video_link():
            eid = talk.event_id  # type: ignore[attr-defined]
            recorded_by_event[eid] = recorded_by_event.get(eid, 0) + 1

    # Aggregate counts per event in two queries
    total_by_event = dict(
        Talk.objects.filter(event_id__in=event_ids)
        .values_list("event_id")
        .annotate(cnt=Count("id"))
        .values_list("event_id", "cnt"),
    )
    today_by_event = dict(
        Talk.objects.filter(event_id__in=event_ids, start_time__date=current_date)
        .values_list("event_id")
        .annotate(cnt=Count("id"))
        .values_list("event_id", "cnt"),
    )

    # Build per-event rows
    event_rows = []
    for event in events:
        eid = event.id  # type: ignore[attr-defined]
        event_rows.append(
            {
                "name": event.name,
                "total": total_by_event.get(eid, 0),
                "today": today_by_event.get(eid, 0),
                "recorded": recorded_by_event.get(eid, 0),
            },
        )

    totals = {
        "total": sum(cast("int", r["total"]) for r in event_rows),
        "today": sum(cast("int", r["today"]) for r in event_rows),
        "recorded": sum(cast("int", r["recorded"]) for r in event_rows),
    }

    context = {
        "event_rows": event_rows,
        "totals": totals,
        "single_event": len(event_rows) == 1,
    }
    return render(request, "talks/partials/dashboard_stats.html", context)


@require_safe
@cache_page(30)  # Cache for 30 seconds - talks list changes infrequently
@vary_on_cookie
def upcoming_talks(request: HttpRequest) -> HttpResponse:
    """Display the next 8 upcoming talks, scoped to the user's events."""
    user = cast("CustomUser", request.user)
    current_time = timezone.now()
    talks_qs = (
        Talk.objects.select_related("room")
        .prefetch_related("speakers")
        .filter(start_time__gt=current_time)
        .accessible_to(user)
    )
    # ``with_streamings`` avoids an N+1 when the template calls
    # ``get_transcription_url`` / ``get_video_link`` (both fall back to ``streaming``).
    talks = talks_qs.with_rating_stats().order_by("start_time")[:8].with_streamings()
    saved_talk_ids: set[int] = set()
    if request.user.is_authenticated:
        saved_talk_ids = SavedTalk.talk_ids_for(cast("CustomUser", request.user))
    # Pass real dates for the Today/Tomorrow badges: the template cannot do date arithmetic
    # (chaining |date|add on the {% now %} string never produced a valid date).
    today = timezone.localdate()
    context = {
        "upcoming_talks": talks,
        "saved_talk_ids": saved_talk_ids,
        "today": today.isoformat(),
        "tomorrow": (today + timedelta(days=1)).isoformat(),
        "show_rating_summary": _can_see_rating_summary(
            request.user,
            resolve_default_event(request),
        ),
    }
    return render(request, "talks/partials/upcoming_talks.html", context)


@require_safe
def talk_redirect_view(request: HttpRequest, talk_id: str) -> HttpResponse:
    """Get talk detail view by Talk ID or Pretalx ID."""
    talk = get_talk_by_id_or_pretalx(talk_id, user=cast("CustomUser", request.user))
    if talk:
        return redirect("talk_detail", pk=talk.pk)
    raise Http404


def _apply_status_filter(queryset: TalkQuerySet, status: str) -> TalkQuerySet:
    """Filter talks by timing status (current, upcoming, completed)."""
    now = timezone.now()
    margin = timedelta(minutes=5)

    if status == "current":
        return queryset.filter(
            start_time__lte=now + margin,
            end_time__gte=now - margin,
        )
    if status == "upcoming":
        return queryset.filter(start_time__gt=now + margin)
    if status == "completed":
        return queryset.filter(end_time__lt=now - margin)

    return queryset


def _apply_search_filter(queryset: TalkQuerySet, request: HttpRequest) -> TalkQuerySet:
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
