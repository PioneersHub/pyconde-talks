"""
Views for managing and displaying Talk objects.

This module provides class-based and function-based views for handling Talk-related operations,
including listing, detail views, and statistics.
"""

from typing import Any

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import IntegrityError
from django.db.models import Avg, Count, Q
from django.db.models.functions import TruncDate
from django.db.models.query import QuerySet
from django.http import Http404, HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST
from django.views.generic import DetailView, ListView

from .models import Rating, Room, Talk
from .utils import get_talk_by_id_or_pretalx


# Constants
MIN_RATING_SCORE = 1
MAX_RATING_SCORE = 5


class TalkDetailView(LoginRequiredMixin, DetailView):
    """
    Display detailed information about a specific Talk.

    Requires user authentication to access the view.
    """

    model = Talk
    template_name = "talks/talk_detail.html"
    context_object_name = "talk"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        """Enhance the template context with rating information."""
        context = super().get_context_data(**kwargs)
        talk = self.object

        # Get rating statistics
        ratings = Rating.objects.filter(talk=talk)
        context["rating_count"] = ratings.count()
        context["average_rating"] = ratings.aggregate(avg=Avg("score"))["avg"]

        # Get user's existing rating if any
        if self.request.user.is_authenticated:
            try:
                context["user_rating"] = Rating.objects.get(
                    talk=talk,
                    user=self.request.user,
                )
            except Rating.DoesNotExist:
                context["user_rating"] = None

        return context


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
        """Get the list of talks filtered by room, date, track, presentation type, and query."""
        queryset: QuerySet[Talk] = Talk.objects.all()

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

        # Annotate with rating statistics
        queryset = queryset.annotate(
            average_rating=Avg("ratings__score"),
            rating_count=Count("ratings"),
        )

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
    upcoming_talks = Talk.objects.filter(start_time__gt=current_time).order_by("start_time")[:8]
    context = {"upcoming_talks": upcoming_talks}
    return render(request, "talks/partials/upcoming_talks.html", context)


def talk_redirect_view(_: HttpRequest, talk_id: str) -> HttpResponse:
    """Get talk detail view by Talk ID or Pretalx ID."""
    talk = get_talk_by_id_or_pretalx(talk_id)
    if talk:
        return redirect("talk_detail", pk=talk.pk)
    msg = f"No talk found with ID or Pretalx ID: {talk_id}"
    raise Http404(msg)


@login_required
@require_POST
def rate_talk(request: HttpRequest, talk_id: int) -> HttpResponse:
    """
    Handle talk rating submission.

    Users can rate a talk from 1 to 5 stars, with an optional comment.
    Users can update their existing rating if they already rated the talk.
    """
    talk = get_object_or_404(Talk, pk=talk_id)
    score = request.POST.get("score")
    comment = request.POST.get("comment", "").strip()

    # Validate score
    try:
        score_int = int(score)
        if score_int < MIN_RATING_SCORE or score_int > MAX_RATING_SCORE:
            messages.error(
                request,
                f"Rating must be between {MIN_RATING_SCORE} and {MAX_RATING_SCORE} stars.",
            )
            return redirect("talk_detail", pk=talk_id)
    except (TypeError, ValueError):
        messages.error(request, "Invalid rating value.")
        return redirect("talk_detail", pk=talk_id)

    # Create or update rating
    try:
        rating, created = Rating.objects.update_or_create(
            talk=talk,
            user=request.user,
            defaults={"score": score_int, "comment": comment},
        )
        if created:
            messages.success(request, "Your rating has been submitted!")
        else:
            messages.success(request, "Your rating has been updated!")
    except IntegrityError:
        messages.error(request, "Error submitting rating. Please try again.")

    return redirect("talk_detail", pk=talk_id)


@login_required
def get_talk_rating_stats(request: HttpRequest, talk_id: int) -> JsonResponse:
    """
    Return rating statistics for a talk as JSON.

    Returns:
    - average_rating: Average rating score (1-5)
    - rating_count: Total number of ratings
    - user_rating: Current user's rating if exists (score and comment)

    """
    talk = get_object_or_404(Talk, pk=talk_id)

    # Get aggregate stats
    stats = Rating.objects.filter(talk=talk).aggregate(
        average=Avg("score"),
        count=Count("id"),
    )

    # Get user's rating
    user_rating = None
    if request.user.is_authenticated:
        try:
            rating = Rating.objects.get(talk=talk, user=request.user)
            user_rating = {
                "score": rating.score,
                "comment": rating.comment,
            }
        except Rating.DoesNotExist:
            pass

    return JsonResponse(
        {
            "average_rating": round(stats["average"], 1) if stats["average"] else None,
            "rating_count": stats["count"],
            "user_rating": user_rating,
        },
    )
