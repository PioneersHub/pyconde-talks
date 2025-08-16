"""
Conference talk management module for the event talks site.

This module provides custom validators that are used across the Talks model.
"""

from django.core.exceptions import ValidationError

from talks.types import VideoProvider


def validate_video_link(video_link: str) -> bool | Exception:
    """

    Validate video_link field from model.Talks.

    This validator is to be used by video_link field from Talks/model
    """
    valid_video_provider = any(provider in video_link for provider in VideoProvider)

    if not valid_video_provider:
        video_providers_name = ", ".join([x.name for x in VideoProvider])
        allowed_providers = f"Allowed video providers are: {video_providers_name}"
        exception_message = f"URL must be from a valid video provider. {allowed_providers}"
        raise ValidationError(exception_message)
    return True
