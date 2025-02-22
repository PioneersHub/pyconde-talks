"""URL configuration for pyconde_talks project."""

from allauth.account.views import confirm_login_code, logout, request_login_code
from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import path
from django.views.generic import TemplateView

from talks.views import TalkDetailView, TalkListView


urlpatterns = [
    path("admin/", admin.site.urls),
    path("accounts/login/code/", request_login_code, name="account_request_login_code"),
    path("accounts/login/code/confirm/", confirm_login_code, name="account_confirm_login_code"),
    path("accounts/logout/", logout, name="account_logout"),
    path("talks/", TalkListView.as_view(), name="talk_list"),
    path("talks/<int:pk>/", TalkDetailView.as_view(), name="talk_detail"),
    path("", TemplateView.as_view(template_name="home.html"), name="home"),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
