"""URL configuration for the users app."""

from allauth.account.views import confirm_login_code, logout
from django.urls import path

from .views import CustomRequestLoginCodeView, profile_view


urlpatterns = [
    path("login/", CustomRequestLoginCodeView.as_view(), name="account_login"),
    path("login/code/", CustomRequestLoginCodeView.as_view(), name="account_request_login_code"),
    path("login/code/confirm/", confirm_login_code, name="account_confirm_login_code"),
    path("logout/", logout, name="account_logout"),
    path("profile/", profile_view, name="user_profile"),
]
