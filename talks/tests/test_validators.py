"""Comprehensive tests for talks.validators."""

import pytest
from django.core.exceptions import ValidationError

from talks.validators import validate_video_link


class TestValidateVideoLink:
    """Tests for the validate_video_link function."""

    def test_empty_link_valid(self) -> None:
        """Accept an empty link since video_link is optional."""
        validate_video_link("")  # Should not raise

    def test_youtube_link_valid(self) -> None:
        """Accept a valid YouTube URL."""
        validate_video_link("https://youtube.com/watch?v=abc")

    def test_vimeo_link_valid(self) -> None:
        """Accept a valid Vimeo URL."""
        validate_video_link("https://vimeo.com/12345")

    def test_youtu_be_link_valid(self) -> None:
        """Accept a shortened youtu.be URL."""
        validate_video_link("https://youtu.be/abc")

    def test_invalid_provider(self) -> None:
        """Reject URLs from unsupported video providers."""
        with pytest.raises(ValidationError, match="valid video provider"):
            validate_video_link("https://dailymotion.com/video/123")

    def test_no_domain(self) -> None:
        """Reject a URL with no recognizable domain."""
        with pytest.raises(ValidationError, match="domain"):
            validate_video_link("/just/a/path")

    def test_subdomain_youtube(self) -> None:
        """Accept YouTube URLs served from subdomains like www.youtube.com."""
        validate_video_link("https://www.youtube.com/watch?v=abc")
