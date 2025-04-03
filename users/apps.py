"""AppConfig subclass for the users application."""

from django.apps import AppConfig


class UsersConfig(AppConfig):
    """Configuration class for the users application."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "users"
