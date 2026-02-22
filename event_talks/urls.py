"""URL configuration for event_talks project."""

from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path
from django.views.generic import TemplateView
from health_check.views import HealthCheckView


urlpatterns = [
    path("admin/", admin.site.urls),
    path("accounts/", include("users.urls")),
    path("talks/", include("talks.urls")),
    path("", TemplateView.as_view(template_name="home.html"), name="home"),
    path(
        "ht/",
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
]

if settings.DEBUG:  # pragma: no cover
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
