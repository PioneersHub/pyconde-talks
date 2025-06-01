"""
Unit tests for specific methods of the talks app models.

These tests focus on the _enrich_video_link, video_provider,
and video_link validation methods of the Talk model.
"""

from datetime import UTC, datetime, timedelta

import pytest
from django.core.exceptions import ValidationError
from django.test import override_settings
from model_bakery import baker

from talks.models import Talk
from talks.types import VideoProvider


@pytest.mark.django_db
class TestTalkModel:
    """Test cases for specific Talk model methods."""

    @pytest.mark.parametrize(
        ("initial_link", "expected_link"),
        [
            (
                "https://youtube.com/watch?v=test",
                "https://youtube.com/watch?v=test&enablejsapi=1",
            ),
            ("https://youtube.com/watch", "https://youtube.com/watch?enablejsapi=1"),
            ("https://vimeo.com/test", "https://vimeo.com/test"),
            ("", ""),
        ],
    )
    def test_enrich_video_link(self, initial_link: str, expected_link: str) -> None:
        """Test the _enrich_video_link method ensures correct query parameters for YouTube."""
        start_time = datetime.now(tz=UTC) + timedelta(days=1)
        talk = baker.prepare(Talk, video_link=initial_link, start_time=start_time)

        with override_settings(SHOW_UPCOMING_TALKS_LINKS=True):
            talk.save()
            assert talk.video_link == expected_link

    @pytest.mark.parametrize(
        ("video_link", "expected_provider"),
        [
            ("https://youtube.com/watch?v=test", VideoProvider.Youtube.name),
            ("https://vimeo.com/test", VideoProvider.Vimeo.name),
            ("", ""),
        ],
    )
    def test_video_provider(self, video_link: str, expected_provider: str) -> None:
        """Test the video_provider property correctly identifies the video platform."""
        talk = baker.prepare(Talk, video_link=video_link)
        talk.save()

        with override_settings(SHOW_UPCOMING_TALKS_LINKS=True):
            assert talk.video_provider == expected_provider

    @pytest.mark.parametrize(
        ("video_link", "expected_validation_error"),
        [
            ("http://invalid-video-link.com", True),
            ("https://vimeo.com/testvideo", False),
            ("https://youtube.com/testvideo", False),  # Valid YouTube link
            ("", False),  # Empty link should not raise a validation error
        ],
    )
    def test_video_link_validation(
        self,
        video_link: str,
        *,
        expected_validation_error: bool,
    ) -> None:
        """Test that video_link validation correctly handles valid and invalid providers."""
        talk = baker.prepare(Talk, video_link=video_link)
        if expected_validation_error:
            with pytest.raises(ValidationError, match="URL must be from a valid video provider."):
                talk.full_clean()
        else:
            try:
                talk.full_clean()
            except ValidationError:
                pytest.fail("ValidationError raised unexpectedly")
