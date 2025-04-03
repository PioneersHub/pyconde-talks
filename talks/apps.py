"""Defines the configuration for the Talks app."""

from django.apps import AppConfig


class TalksConfig(AppConfig):
    """Configuration class for the Talks app."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "talks"
