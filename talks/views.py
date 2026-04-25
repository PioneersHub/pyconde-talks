"""
Views for managing and displaying Talk objects.

This module provides class-based and function-based views for handling Talk-related operations,
including listing, detail views, and statistics.
"""

from datetime import timedelta
from typing import TYPE_CHECKING, Any, cast

from django.contrib import messages
from django.db import IntegrityError
from django.db.models import Avg, Count, F, Q
from django.db.models.functions import TruncDate
from django.http import Http404, HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views.decorators.cache import cache_page
from django.views.decorators.http import require_POST, require_safe
from django.views.decorators.vary import vary_on_cookie
from django.views.generic import DetailView, ListView

from events.models import Event
from events.session import resolve_default_event

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


def _can_see_rating_summary(user: Any, event: Event | None) -> bool:
    """Return True if the user may see aggregate rating stats for this event."""
    if getattr(user, "is_staff", False) or getattr(user, "is_superuser", False):
        return True
    if event is None:
        return True
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
        return Talk.objects.select_related("room").prefetch_related("speakers").accessible_to(user)

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
        if self.request.headers.get("HX-Request"):
            return ["talks/talk_list.html#talk-list"]
        return [cast("str", self.template_name)]  # type: ignore[redundant-cast]

    def get_queryset(self) -> QuerySet[Talk]:
        """Get the list of talks filtered by room, date, track, presentation type, and query."""
        queryset = self._base_queryset()

        queryset = self._apply_event_filter(queryset)
        queryset = self._apply_list_filters(queryset)
        queryset = _apply_search_filter(queryset, self.request)

        # Annotate with rating statistics for list display
        queryset = queryset.annotate(
            average_rating=Avg("ratings__score"),
            rating_count=Count("ratings"),
        )

        return queryset.order_by("start_time")

    def _base_queryset(self) -> QuerySet[Talk]:
        """Return talks scoped to user access with list-view optimizations."""
        # Defer large text fields not needed in list view to reduce memory usage
        user = cast("CustomUser", self.request.user)
        return (
            Talk.objects.select_related("room")
            .prefetch_related("speakers")
            .defer("description", "abstract")
            .accessible_to(user)
        )

    def _filter_options_queryset(self) -> QuerySet[Talk]:
        """Return talks used to build filter options for the selected event/search scope."""
        queryset = self._base_queryset()
        queryset = self._apply_event_filter(queryset)
        return _apply_search_filter(queryset, self.request)

    def _apply_event_filter(self, queryset: QuerySet[Talk]) -> QuerySet[Talk]:
        """Filter talks by event. Defaults to the current event from session/settings."""
        event_id = self.request.GET.get("event", "")
        if event_id == "all":
            return queryset
        if event_id:
            queryset = queryset.filter(event_id=event_id)
        else:
            # No explicit selection: default to the resolved current event
            default_event = resolve_default_event(self.request)
            if default_event:
                queryset = queryset.filter(event=default_event)
        return queryset

    def _apply_list_filters(self, queryset: QuerySet[Talk]) -> QuerySet[Talk]:
        """
        Apply room, date, track, type, and saved filters from GET params.

        Each value is validated against the event-scoped ``queryset`` to discard stale params left
        over from a previous event switch.
        All valid params are then applied together in a single ``.filter()`` so that intersecting
        criteria (e.g. Room A + April 6) correctly produce an empty result when no talks match both.
        """
        active: dict[str, str] = {}

        room_id = self.request.GET.get("room")
        if room_id and queryset.filter(room_id=room_id).exists():
            active["room_id"] = room_id

        date_value = self.request.GET.get("date")
        if date_value and queryset.filter(start_time__date=date_value).exists():
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
        if user.is_superuser:
            context["events"] = Event.objects.filter(is_active=True).order_by("name")
        else:
            context["events"] = user.events.filter(is_active=True).order_by("name")

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
        context["is_htmx_request"] = bool(self.request.headers.get("HX-Request"))

        return context

    def _resolve_selected_event(self) -> Event | None:
        """Return the currently filtered event, or the default event."""
        event_param = self.request.GET.get("event", "")
        if event_param and event_param != "all":
            return Event.objects.filter(pk=event_param).first()
        if not event_param:
            return resolve_default_event(self.request)
        return None


@require_safe
@cache_page(60)  # Cache for 60 seconds to reduce database queries
@vary_on_cookie
def dashboard_stats(request: HttpRequest) -> HttpResponse:
    """Generate per-event statistics for the dashboard, respecting user access."""
    user = cast("CustomUser", request.user)
    current_date = timezone.now().date()

    # Determine which events the user may see
    if user.is_superuser:
        events = Event.objects.filter(is_active=True).order_by("name")
    else:
        events = user.events.filter(is_active=True).order_by("name")

    event_ids = list(events.values_list("id", flat=True))

    # Fetch only the fields needed for get_video_link() - scoped to user events
    talks_for_video = (
        Talk.objects.filter(event_id__in=event_ids)
        .select_related("room")
        .only("id", "video_link", "start_time", "duration", "room", "room__id", "event")
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
    )
    if not user.is_superuser:
        accessible_event_ids = user.events.values_list("id", flat=True)
        talks_qs = talks_qs.filter(event_id__in=accessible_event_ids)
    talks = talks_qs.annotate(
        average_rating=Avg("ratings__score"),
        rating_count=Count("ratings"),
    ).order_by("start_time")[:8]
    saved_talk_ids: set[int] = set()
    if request.user.is_authenticated:
        saved_talk_ids = SavedTalk.talk_ids_for(cast("CustomUser", request.user))
    context = {
        "upcoming_talks": talks,
        "saved_talk_ids": saved_talk_ids,
        "show_rating_summary": _can_see_rating_summary(
            request.user,
            resolve_default_event(request),
        ),
    }
    return render(request, "talks/partials/upcoming_talks.html", context)


@require_safe
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
    stats = talk.get_rating_stats()
    user_rating = Rating.objects.filter(talk=talk, user=request.user).first()
    show_summary = _can_see_rating_summary(request.user, talk.event)
    context = {
        "talk": talk,
        "average_rating": stats.average if show_summary else None,
        "rating_count": stats.total if show_summary else 0,
        "user_rating": user_rating,
        "show_comment_form": not is_comment_save,
        "show_rating_summary": show_summary,
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


def _apply_status_filter(queryset: QuerySet[Talk], status: str) -> QuerySet[Talk]:
    """Filter talks by timing status (current, upcoming, completed)."""
    now = timezone.now()
    margin = timedelta(minutes=5)

    if status == "current":
        queryset = queryset.annotate(
            _end_time=F("start_time") + F("duration"),
        ).filter(
            start_time__lte=now + margin,
            _end_time__gte=now - margin,
        )
    elif status == "upcoming":
        queryset = queryset.filter(start_time__gt=now + margin)
    elif status == "completed":
        queryset = queryset.annotate(
            _end_time=F("start_time") + F("duration"),
        ).filter(
            _end_time__lt=now - margin,
        )

    return queryset


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
    except TypeError, ValueError:  # fmt: skip
        return _rating_error_response(request, talk_id, _("Invalid rating value."), is_htmx=is_htmx)

    if score < MIN_RATING_SCORE or score > MAX_RATING_SCORE:
        return _rating_error_response(
            request,
            talk_id,
            _("Rating must be between %(min)s and %(max)s stars.")
            % {"min": MIN_RATING_SCORE, "max": MAX_RATING_SCORE},
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
            msg = (
                _("Your rating has been submitted!")
                if created
                else _("Your rating has been updated!")
            )
            messages.success(request, msg)
    except IntegrityError:
        return _rating_error_response(
            request,
            talk_id,
            _("Error submitting rating. Please try again."),
            is_htmx=is_htmx,
            status=500,
        )

    if is_htmx:
        return _render_rating_htmx_response(request, talk, is_comment_save=is_comment_save)

    return redirect("talk_detail", pk=talk_id)


@require_POST
def delete_rating(request: HttpRequest, talk_id: int) -> HttpResponse:
    """
    Delete the current user's rating for a talk.

    Returns a partial HTML fragment for HTMX requests or redirects otherwise.
    """
    talk = get_object_or_404(Talk, pk=talk_id)
    is_htmx = request.headers.get("HX-Request") == "true"
    deleted_count, _detail = Rating.objects.filter(talk=talk, user=request.user).delete()

    if not is_htmx:
        if deleted_count:
            messages.success(request, _("Your rating has been removed."))
        else:
            messages.info(request, _("No rating to remove."))
        return redirect("talk_detail", pk=talk_id)

    return _render_rating_htmx_response(request, talk, is_comment_save=False)


@require_safe
def get_talk_rating_stats(request: HttpRequest, talk_id: int) -> JsonResponse:
    """
    Return rating statistics for a talk as JSON.

    Returns average_rating, rating_count, and the current user's rating if it exists.
    """
    talk = get_object_or_404(Talk, pk=talk_id)

    stats = talk.get_rating_stats()

    user_rating = None
    if request.user.is_authenticated:
        rating = Rating.objects.filter(talk=talk, user=request.user).first()
        if rating:
            user_rating = {
                "score": rating.score,
                "comment": rating.comment,
            }

    show_summary = _can_see_rating_summary(request.user, talk.event)
    return JsonResponse(
        {
            "average_rating": (round(stats.average, 1) if show_summary and stats.average else None),
            "rating_count": stats.total if show_summary else 0,
            "user_rating": user_rating,
        },
    )


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
        # Schedule cards use a compact icon-only partial (no text label).
        hx_target = request.headers.get("HX-Target", "")
        template = (
            "talks/partials/schedule_save_button.html"
            if hx_target.startswith("sched-save-")
            else "talks/partials/save_button.html"
        )
        return render(
            request,
            template,
            {"talk": talk, "is_saved": is_saved},
        )

    if is_saved:
        messages.success(request, _("Talk saved!"))
    else:
        messages.info(request, _("Talk removed from saved."))
    return redirect("talk_detail", pk=talk_id)
