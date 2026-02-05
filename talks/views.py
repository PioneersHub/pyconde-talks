"""
Views for managing and displaying Talk objects.

This module provides class-based and function-based views for handling Talk-related operations,
including listing, detail views, and statistics.
"""

from typing import TYPE_CHECKING, Any

from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Q
from django.db.models.functions import TruncDate
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.cache import cache_page
from django.views.generic import DetailView, ListView

from .models import Room, Talk
from .utils import get_talk_by_id_or_pretalx


if TYPE_CHECKING:
    from django.db.models.query import QuerySet


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
        return Talk.objects.select_related("room").prefetch_related("speakers")


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

        # Filter by room
        room = self.request.GET.get("room")
        if room:
            queryset = queryset.filter(room_id=room)

        # Filter by date
        date = self.request.GET.get("date")
        if date:
            queryset = queryset.filter(start_time__date=date)

        # Filter by track
        track = self.request.GET.get("track")
        if track:
            queryset = queryset.filter(track=track)

        # Filter by presentation type
        presentation_type = self.request.GET.get("presentation_type")
        if presentation_type:
            queryset = queryset.filter(presentation_type=presentation_type)

        # Free-text search with scope
        query = (self.request.GET.get("q") or "").strip()
        raw_scopes = [s.strip() for s in self.request.GET.getlist("search_in") if s.strip()]
        scopes = set(raw_scopes or ["all"])  # default to all when nothing selected
        if query:
            q_obj = Q()
            if "all" in scopes or not scopes:
                scopes = {"title", "author", "description"}
            if "title" in scopes:
                q_obj |= Q(title__icontains=query)
            if "description" in scopes:
                q_obj |= Q(description__icontains=query) | Q(abstract__icontains=query)
            if "author" in scopes:
                q_obj |= Q(speakers__name__icontains=query)

            queryset = queryset.filter(q_obj).distinct()

        return queryset.order_by("start_time")

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

        return context


@login_required
@cache_page(60)  # Cache for 60 seconds to reduce database queries
def dashboard_stats(request: HttpRequest) -> HttpResponse:
    """Generate statistics for the dashboard."""
    current_time = timezone.now()

    context = {
        "total_talks": Talk.objects.count(),
        "todays_talks": Talk.objects.filter(start_time__date=current_time.date()).count(),
        "recorded_talks": sum(1 for talk in Talk.objects.all() if talk.get_video_link()),
    }
    return render(request, "talks/partials/dashboard_stats.html", context)


@login_required
def upcoming_talks(request: HttpRequest) -> HttpResponse:
    """Display the next 8 upcoming talks."""
    current_time = timezone.now()
    talks = (
        Talk.objects.select_related("room")
        .prefetch_related("speakers")
        .filter(start_time__gt=current_time)
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
