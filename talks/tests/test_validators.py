"""
Conference talk management module for the event talks site.

This module provides unit tests for the custom validators that are used across the Talks model.
"""

import pytest
from django.core.exceptions import ValidationError

from talks.validators import validate_video_link


class TestValidators:
    """TestValidators implement unit tests for all validators."""

    def test_validate_video_link_with_unknown_video_provider(self) -> None:
        """Test validate_video_link when it raises an exception."""
        video_link = "http://unknown-video-link.com"
        exception = (
            "URL must be from a valid video provider. Allowed video providers are: Youtube, Vimeo"
        )
        with pytest.raises(ValidationError) as exception_info:
            validate_video_link(video_link)

        assert exception == next(iter(exception_info.value))

    def test_validate_video_link_with_valid_video_provider(self) -> None:
        """Test validate_video_link when it raises an exception."""
        video_links = ["https://youtube.com", "https://vimeo.com"]

        for video_link in video_links:
            assert validate_video_link(video_link)
