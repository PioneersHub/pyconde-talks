"""
Conference talk management module for the event talks site.

This module provides custom validators that are used across the Talks model.
"""

from urllib.parse import urlparse

from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

from talks.types import VideoProvider


def validate_video_link(video_link: str) -> None:
    """
    Validate video_link field from model.Talks.

    Raises ValidationError if the video link is not from a valid provider.
    """
    if not video_link:
        return

    try:
        parsed = urlparse(video_link)
        hostname = parsed.netloc.lower()
    except ValueError as exc:  # pragma: no cover
        raise ValidationError(
            _("Invalid URL format"),
            code="invalid_url",
        ) from exc

    if not hostname:
        raise ValidationError(
            _("URL must include a domain"),
            code="missing_domain",
        )

    # Check if hostname ends with provider domain or is exact match
    valid_video_provider = any(
        hostname == provider.value.lower() or hostname.endswith(f".{provider.value.lower()}")
        for provider in VideoProvider
    )

    if not valid_video_provider:
        video_providers_name = ", ".join([x.name for x in VideoProvider])
        raise ValidationError(
            _("URL must be from a valid video provider. Allowed providers: %(providers)s"),
            code="invalid_provider",
            params={"providers": video_providers_name},
        )
