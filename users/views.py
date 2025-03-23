"""Views for rendering the home page with talk statistics."""

from typing import Any

from django.utils import timezone
from django.views.generic import TemplateView

from .models import Talk


class HomeView(TemplateView):
    """Home page view that displays talk statistics for authenticated users."""

    template_name = "home.html"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:  # noqa: ANN401
        """
        Add talk statistics to the template context for authenticated users.

        Args:
            **kwargs: Additional context variables.

        Returns:
            dict[str, Any]: Updated context dictionary.

        """
        context = super().get_context_data(**kwargs)
        if self.request.user.is_authenticated:
            today = timezone.now().date()
            context.update(
                {
                    "total_talks": Talk.objects.count(),
                    "todays_talks": Talk.objects.filter(date_time__date=today).count(),
                    "recorded_talks": Talk.objects.exclude(video_link="").count(),
                    "upcoming_talks": Talk.objects.filter(
                        date_time__gte=timezone.now(),
                    ).order_by("date_time")[:5],
                },
            )
        return context
