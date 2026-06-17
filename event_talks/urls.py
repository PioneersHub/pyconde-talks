"""URL configuration for event_talks project."""

from django.conf import settings
from django.contrib import admin
from django.contrib.auth.decorators import login_not_required
from django.urls import include, path
from django.views.generic import TemplateView
from django.views.static import serve
from health_check.views import HealthCheckView

from users.views import set_language


urlpatterns = [
    # Honor DJANGO_ADMIN_URL so operators can relocate the admin off the well-known /admin/ path.
    path(settings.ADMIN_URL, admin.site.urls),
    # Language switcher endpoint. Wrapped in ``login_not_required`` so anonymous visitors on the
    # login page can switch language too (LoginRequiredMiddleware would otherwise block it).
    path("i18n/setlang/", set_language, name="set_language"),
    path("accounts/", include("users.urls")),
    path("talks/", include("talks.urls")),
    path(
        "",
        login_not_required(TemplateView.as_view(template_name="home.html")),
        name="home",
    ),
    path(
        "ht/",
        login_not_required(
            HealthCheckView.as_view(
                # This endpoint is unauthenticated and hit on every Docker/compose/deploy
                # liveness probe, so it stays cheap and self-contained. The Mail check is
                # deliberately excluded: it opens a real SMTP/Mailgun connection on each hit,
                # which would (1) let anyone drive outbound mail-backend connections and
                # (2) flip the container to "unhealthy" during an unrelated ESP outage,
                # triggering false deploy rollbacks. Monitor mail deliverability separately.
                checks=[
                    "health_check.Cache",
                    "health_check.Database",
                    "health_check.Storage",
                    # 3rd party checks
                    "health_check.contrib.psutil.Disk",
                    "health_check.contrib.psutil.Memory",
                ],
            ),
        ),
    ),
]

if settings.DEBUG or settings.SERVE_STATIC_LOCALLY:  # pragma: no cover
    # Serve static and media files locally when in DEBUG mode or if explicitly enabled via env var.
    urlpatterns += [
        path(
            f"{settings.STATIC_URL.lstrip('/')}<path:path>",
            login_not_required(serve),
            {"document_root": settings.STATIC_ROOT},
        ),
        path(
            f"{settings.MEDIA_URL.lstrip('/')}<path:path>",
            login_not_required(serve),
            {"document_root": settings.MEDIA_ROOT},
        ),
    ]
