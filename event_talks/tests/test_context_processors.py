"""Tests for event_talks.context_processors."""

from django.test import RequestFactory, override_settings

from event_talks.context_processors import branding


class TestBrandingContextProcessor:
    """Tests for the branding context processor."""

    @override_settings(
        BRAND_EVENT_NAME="PyCon DE",
        BRAND_EVENT_YEAR="2025",
        PRETALX_BASE_URL="https://pretalx.com",
        PRETALX_EVENT_SLUG="pycon2025",
        BRAND_MAIN_WEBSITE_URL="https://pycon.de",
        BRAND_VENUE_URL="https://venue.example.com",
        BRAND_LOGO_SVG_NAME="pycon_logo",
        BRAND_ASSETS_SUBDIR="pycon2025",
        BRAND_MADE_BY_NAME="PioneersHub",
        BRAND_MADE_BY_URL="https://pioneershub.org",
    )
    def test_branding_full(self) -> None:
        """Full branding settings produce correct context values."""
        request = RequestFactory().get("/")
        ctx = branding(request)
        assert ctx["brand_event_name"] == "PyCon DE"
        assert ctx["brand_event_year"] == "2025"
        assert ctx["brand_full_name"] == "PyCon DE 2025"
        assert ctx["brand_title"] == "PyCon DE 2025 Talks"
        assert ctx["brand_meta_description"] == "PyCon DE 2025 Talks and Schedule"
        assert ctx["pretalx_schedule_url"] == "https://pretalx.com/pycon2025/schedule/"
        assert ctx["pretalx_speakers_url"] == "https://pretalx.com/pycon2025/speaker/"
        assert ctx["brand_main_website_url"] == "https://pycon.de"

    @override_settings(
        BRAND_EVENT_NAME="",
        BRAND_EVENT_YEAR="",
        PRETALX_BASE_URL="https://pretalx.com",
        PRETALX_EVENT_SLUG="",
    )
    def test_branding_empty(self) -> None:
        """Empty branding settings produce safe defaults."""
        request = RequestFactory().get("/")
        ctx = branding(request)
        assert ctx["brand_full_name"] == ""
        assert ctx["brand_title"] == "Talks"
        assert ctx["brand_meta_description"] == "Talks and Schedule"
        assert ctx["pretalx_schedule_url"] == ""
        assert ctx["pretalx_speakers_url"] == ""
