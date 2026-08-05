"""
Conference talk management module for the event talks site.

This module provides unit test for utils.url.
"""

import pytest

from utils.url import add_query_param, youtube_embed_url, youtube_video_id


VIDEO_ID = "Z7Xlj2eG8sc"
EMBED_URL = f"https://www.youtube.com/embed/{VIDEO_ID}"


class TestURL:
    """TestURL implements unit tests for all utils.url."""

    def test_add_query_param_change_url(self) -> None:
        """Test if add_query_param adds a new query param accordingly."""
        url = "http://abc.com"

        new_url = add_query_param(url, "new_param", "new_value")

        assert "?new_param=new_value" in new_url

        new_url = add_query_param(new_url, "new_param_2", "new_value_2")

        assert "&new_param_2=new_value_2" in new_url


class TestYoutubeVideoId:
    """Verify youtube_video_id reads the ID out of every link shape we receive."""

    @pytest.mark.parametrize(
        "url",
        [
            f"https://youtu.be/{VIDEO_ID}",
            f"http://youtu.be/{VIDEO_ID}",
            f"https://youtu.be/{VIDEO_ID}?enablejsapi=1",
            f"https://youtu.be/{VIDEO_ID}?t=90&si=share-token",
            f"https://www.youtube.com/watch?v={VIDEO_ID}",
            f"https://youtube.com/watch?v={VIDEO_ID}&list=PL123",
            f"https://www.youtube.com/embed/{VIDEO_ID}",
            f"https://www.youtube.com/shorts/{VIDEO_ID}",
            f"https://www.youtube.com/live/{VIDEO_ID}",
            f"https://www.youtube.com/v/{VIDEO_ID}",
        ],
        ids=[
            "short",
            "short-http",
            "short-enriched",
            "short-shared",
            "watch",
            "watch-playlist",
            "embed",
            "shorts",
            "live",
            "legacy-v",
        ],
    )
    def test_supported_forms(self, url: str) -> None:
        """Return the video ID for every YouTube URL shape."""
        assert youtube_video_id(url) == VIDEO_ID

    @pytest.mark.parametrize(
        "url",
        [
            "",
            "https://vimeo.com/123456789",
            "https://player.vimeo.com/video/111",
            "https://youtu.be/",
            "https://youtu.be/too-short",
            "https://www.youtube.com/@pyconde",
            "https://youtube.com/watch?v=abc",
            "https://notyoutube.com/watch?v=Z7Xlj2eG8sc",
        ],
        ids=[
            "empty",
            "vimeo",
            "vimeo-player",
            "no-id",
            "malformed-id",
            "channel",
            "short-id",
            "lookalike-host",
        ],
    )
    def test_unsupported_forms(self, url: str) -> None:
        """Return an empty string when there is no YouTube video ID to read."""
        assert youtube_video_id(url) == ""


class TestYoutubeEmbedUrl:
    """Verify youtube_embed_url converts to the one form YouTube lets us frame."""

    @pytest.mark.parametrize(
        "url",
        [
            f"https://youtu.be/{VIDEO_ID}",
            f"https://www.youtube.com/watch?v={VIDEO_ID}",
            f"https://www.youtube.com/shorts/{VIDEO_ID}",
            EMBED_URL,
        ],
        ids=["short", "watch", "shorts", "already-embed"],
    )
    def test_converts_to_embed(self, url: str) -> None:
        """Every recognized form comes out as the embeddable URL."""
        assert youtube_embed_url(url) == EMBED_URL

    def test_keeps_query_parameters(self) -> None:
        """Carry over parameters the player needs, such as enablejsapi."""
        assert youtube_embed_url(f"https://youtu.be/{VIDEO_ID}?enablejsapi=1") == (
            f"{EMBED_URL}?enablejsapi=1"
        )

    def test_translates_the_time_offset(self) -> None:
        """A shared link's ``t`` offset becomes the ``start`` the embedded player reads."""
        assert youtube_embed_url(f"https://youtu.be/{VIDEO_ID}?t=90") == f"{EMBED_URL}?start=90"
        assert (
            youtube_embed_url(f"https://www.youtube.com/watch?v={VIDEO_ID}&t=90s")
            == f"{EMBED_URL}?start=90"
        )

    def test_drops_sharing_noise(self) -> None:
        """Leave out parameters that mean nothing to the embedded player."""
        assert youtube_embed_url(f"https://youtu.be/{VIDEO_ID}?si=share-token") == EMBED_URL

    @pytest.mark.parametrize(
        "url",
        [
            "",
            "https://vimeo.com/123456789",
            "https://player.vimeo.com/video/111",
            "https://www.youtube.com/@pyconde",
        ],
        ids=["empty", "vimeo", "vimeo-player", "channel"],
    )
    def test_passes_other_links_through(self, url: str) -> None:
        """Anything that is not a YouTube video URL is returned untouched."""
        assert youtube_embed_url(url) == url
