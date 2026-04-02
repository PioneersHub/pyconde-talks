"""URL configuration for the users app."""

from allauth.account.views import confirm_login_code, logout
from allauth.socialaccount.providers.discord.urls import urlpatterns as discord_urlpatterns
from django.contrib.auth.decorators import login_not_required
from django.urls import include, path

from .views import CustomRequestLoginCodeView, profile_view


urlpatterns = [
    path("login/", CustomRequestLoginCodeView.as_view(), name="account_login"),
    path("login/code/", CustomRequestLoginCodeView.as_view(), name="account_request_login_code"),
    path(
        "login/code/confirm/",
        login_not_required(confirm_login_code),
        name="account_confirm_login_code",
    ),
    path("logout/", login_not_required(logout), name="account_logout"),
    path("profile/", profile_view, name="user_profile"),
    # Social account management (connected accounts page)
    path("social/", include("allauth.socialaccount.urls")),
]

# Discord OAuth2 login/callback URLs (e.g. accounts/discord/login/)
urlpatterns += discord_urlpatterns
