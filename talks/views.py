from typing import Any

from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models.functions import TruncDate
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from django.views.generic import DetailView, ListView

from .models import Talk


class TalkDetailView(LoginRequiredMixin, DetailView):
    model = Talk
    template_name = "talks/talk_detail.html"
    context_object_name = "talk"


class TalkListView(LoginRequiredMixin, ListView):
    model = Talk
    template_name = "talks/talk_list.html"
    context_object_name = "talks"

    def get_queryset(self) -> Any:
        queryset = Talk.objects.all()

        # Filter by room
        room = self.request.GET.get("room")
        if room and room != "":
            queryset = queryset.filter(room=room)

        # Filter by date
        date = self.request.GET.get("date")
        if date and date != "":
            queryset = queryset.filter(date_time__date=date)

        return queryset.order_by("date_time")

    def get_context_data(self, **kwargs: dict[str, Any]) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        # Get unique rooms
        context["rooms"] = Talk.objects.values_list("room", flat=True).distinct().order_by("room")
        # Get unique dates using distinct on date part of datetime
        context["dates"] = (
            Talk.objects.annotate(
                date=TruncDate("date_time"),
            )
            .values_list(
                "date",
                flat=True,
            )
            .distinct()
            .order_by("date")
        )
        # Add selected filters to context
        context["selected_room"] = self.request.GET.get("room", "")
        context["selected_date"] = self.request.GET.get("date", "")
        return context


@login_required
def dashboard_stats(request):
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
def upcoming_talks(request):
    current_time = timezone.now()

    # Get next 5 upcoming talks
    upcoming_talks = Talk.objects.filter(date_time__gt=current_time).order_by("date_time")[:5]
    context = {"upcoming_talks": upcoming_talks}
    return render(request, "talks/partials/upcoming_talks.html", context)


@login_required
def talk_status(request, pk):
    talk = get_object_or_404(Talk, pk=pk)
    current_time = timezone.now()

    context = {
        "talk": talk,
        "current_time": current_time,
    }

    return render(request, "talks/partials/talk_status.html", context)
