"""App configuration for the events module."""

from django.apps import AppConfig


class EventsConfig(AppConfig):
    """Configuration for the events application."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "events"
    verbose_name = "Events"
