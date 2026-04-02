"""URL configuration for the users app."""

from allauth.account.views import confirm_login_code, logout
from allauth.socialaccount.providers.discord.urls import urlpatterns as discord_urlpatterns
from django.contrib.auth.decorators import login_not_required
from django.urls import include, path

from .views import (
    CustomRequestLoginCodeView,
    add_email_view,
    confirm_add_email_view,
    connections_view,
    delete_account_view,
    profile_view,
)


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
    path("profile/delete/", delete_account_view, name="delete_account"),
    # Email management (for Discord-only users adding an email)
    path("email/add/", add_email_view, name="add_email"),
    path("email/add/confirm/", confirm_add_email_view, name="confirm_add_email"),
    # Social account management (connected accounts page)
    # Override allauth's connections view with ours, keep the rest
    path("social/", connections_view, name="socialaccount_connections"),
    path("social/", include("allauth.socialaccount.urls")),
]

# Discord OAuth2 login/callback URLs (e.g. accounts/discord/login/)
urlpatterns += discord_urlpatterns
