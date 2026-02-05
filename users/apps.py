"""AppConfig subclass for the users application."""

from django.apps import AppConfig


class UsersConfig(AppConfig):
    """Configuration class for the users application."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "users"

    def ready(self) -> None:
        """Django app initialization hook: connect signal handlers."""
        from . import signals  # noqa: F401, PLC0415

        return super().ready()
