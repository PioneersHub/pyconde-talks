"""
Views for managing and displaying Talk objects.

This module provides class-based and function-based views for handling Talk-related operations,
including listing, detail views, and statistics.
"""

from typing import Any

from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models.functions import TruncDate
from django.db.models.query import QuerySet
from django.http import HttpRequest, HttpResponse
from django.shortcuts import render
from django.utils import timezone
from django.views.generic import DetailView, ListView

from .models import Talk


class TalkDetailView(LoginRequiredMixin, DetailView):
    """
    Display detailed information about a specific Talk.

    Requires user authentication to access the view.
    """

    model = Talk
    template_name = "talks/talk_detail.html"
    context_object_name = "talk"


class TalkListView(LoginRequiredMixin, ListView):
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
        """Get the list of talks filtered by room, date, track, and presentation type."""
        queryset: QuerySet[Talk] = Talk.objects.all()

        # Filter by room
        room = self.request.GET.get("room")
        if room and room != "":
            queryset = queryset.filter(room=room)

        # Filter by date
        date = self.request.GET.get("date")
        if date and date != "":
            queryset = queryset.filter(date_time__date=date)

        # Filter by track
        track = self.request.GET.get("track")
        if track and track != "":
            queryset = queryset.filter(track=track)

        # Filter by presentation type
        presentation_type = self.request.GET.get("presentation_type")
        if presentation_type and presentation_type != "":
            queryset = queryset.filter(presentation_type=presentation_type)

        return queryset.order_by("date_time")

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        """Enhance the template context with additional data."""
        context = super().get_context_data(**kwargs)

        # Get unique rooms
        context["rooms"] = Talk.objects.values_list("room", flat=True).distinct().order_by("room")
        # Get unique days
        context["dates"] = (
            Talk.objects.annotate(date=TruncDate("date_time"))
            .values_list("date", flat=True)
            .distinct()
            .order_by("date")
        )
        # Get unique tracks
        context["tracks"] = (
            Talk.objects.values_list("track", flat=True).distinct().order_by("track")
        )
        # Get presentation types
        context["presentation_types"] = Talk.PresentationType.choices
        # Set the selected values for filters
        context["selected_room"] = self.request.GET.get("room", "")
        context["selected_date"] = self.request.GET.get("date", "")
        context["selected_track"] = self.request.GET.get("track", "")
        context["selected_type"] = self.request.GET.get("presentation_type", "")

        return context


@login_required
def dashboard_stats(request: HttpRequest) -> HttpResponse:
    """Generate statistics for the dashboard."""
    current_time = timezone.now()
    context = {
        "total_talks": Talk.objects.count(),
        "todays_talks": Talk.objects.filter(
            date_time__date=current_time.date(),
        ).count(),
        "recorded_talks": Talk.objects.filter(
            video_link__isnull=False,
        ).count(),
    }
    return render(request, "talks/partials/dashboard_stats.html", context)


@login_required
def upcoming_talks(request: HttpRequest) -> HttpResponse:
    """Display the next 5 upcoming talks."""
    current_time = timezone.now()
    upcoming_talks = Talk.objects.filter(date_time__gt=current_time).order_by("date_time")[:5]
    context = {"upcoming_talks": upcoming_talks}
    return render(request, "talks/partials/upcoming_talks.html", context)
