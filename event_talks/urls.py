"""URL configuration for event_talks project."""

from django.conf import settings
from django.contrib import admin
from django.contrib.auth.decorators import login_not_required
from django.urls import include, path
from django.views.generic import TemplateView
from django.views.static import serve
from health_check.views import HealthCheckView


urlpatterns = [
    path("admin/", admin.site.urls),
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
                checks=[
                    "health_check.Cache",
                    "health_check.Database",
                    "health_check.Mail",
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
