"""Tests for event_talks.context_processors."""

from typing import Any

import pytest
from django.contrib.auth.models import AnonymousUser
from django.test import RequestFactory, override_settings

from event_talks.context_processors import branding
from events.models import Event


@pytest.mark.django_db
class TestBrandingContextProcessor:
    """Tests for the branding context processor."""

    def _make_request(self) -> Any:
        request = RequestFactory().get("/")
        request.user = AnonymousUser()
        return request

    @override_settings(DEFAULT_EVENT="")
    def test_branding_full(self) -> None:
        """Event with full branding fields produces correct context values."""
        Event.objects.create(
            name="PyCon 2099",
            slug="pycon-2099",
            year=2099,
            main_website_url="https://pycon.de",
            venue_url="https://venue.example.com",
            logo_svg_name="pycon_logo",
            made_by_name="The PyCon Team",
            made_by_url="https://pycon.de/team",
            pretalx_url="https://pretalx.com/pycon2099",
            is_active=True,
        )
        ctx = branding(self._make_request())
        assert ctx["brand_event_name"] == "PyCon 2099"
        assert ctx["brand_event_year"] == "2099"
        assert ctx["brand_title"] == "PyCon 2099 Talks"
        assert ctx["brand_meta_description"] == "PyCon 2099 Talks and Schedule"
        assert ctx["pretalx_schedule_url"] == "https://pretalx.com/pycon2099/schedule/"
        assert ctx["pretalx_speakers_url"] == "https://pretalx.com/pycon2099/speaker/"
        assert ctx["brand_main_website_url"] == "https://pycon.de"

    @override_settings(DEFAULT_EVENT="")
    def test_branding_empty(self) -> None:
        """No events produce safe defaults."""
        ctx = branding(self._make_request())
        assert ctx["brand_title"] == "Talks"
        assert ctx["brand_meta_description"] == "Talks and Schedule"
        assert ctx["pretalx_schedule_url"] == ""
        assert ctx["pretalx_speakers_url"] == ""

    @override_settings(DEFAULT_EVENT="specific-event")
    def test_branding_uses_default_event(self) -> None:
        """Context processor uses DEFAULT_EVENT setting to find the event."""
        Event.objects.create(
            name="Other Event",
            slug="other",
            year=2025,
            is_active=True,
        )
        evt = Event.objects.create(
            name="Specific Event",
            slug="specific-event",
            year=2025,
            is_active=True,
        )
        ctx = branding(self._make_request())
        assert ctx["brand_event_name"] == evt.name
        assert ctx["brand_event_year"] == "2025"
