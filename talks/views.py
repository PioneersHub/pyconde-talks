from typing import Any

from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models.functions import TruncDate
from django.views.generic import ListView

from .models import Talk


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
