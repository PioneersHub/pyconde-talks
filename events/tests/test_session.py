"""Tests for the shared event-session helpers."""

from typing import Any

import pytest
from django.test import RequestFactory, override_settings
from model_bakery import baker

from events.models import Event
from events.session import (
    SESSION_EVENT_SLUG_KEY,
    get_selected_event_slug,
    resolve_default_event,
    set_selected_event_slug,
)


@pytest.fixture()
def rf() -> RequestFactory:
    """Return a RequestFactory for building test requests."""
    return RequestFactory()


def _make_request_with_session(rf: RequestFactory, session: dict[str, Any] | None = None) -> Any:
    """Build a GET request with a writable session-like attribute for tests."""
    request = rf.get("/")
    request.session = session if session is not None else {}  # type: ignore[assignment]
    return request


class TestGetSelectedEventSlug:
    """Tests for ``get_selected_event_slug``."""

    def test_returns_empty_when_no_session(self, rf: RequestFactory) -> None:
        """A request without a session attribute yields an empty slug."""
        request = rf.get("/")  # no .session attached
        assert get_selected_event_slug(request) == ""

    def test_returns_empty_when_key_missing(self, rf: RequestFactory) -> None:
        """A session without the slug key yields an empty string, not ``None``."""
        request = _make_request_with_session(rf, session={})
        assert get_selected_event_slug(request) == ""

    def test_returns_stored_slug(self, rf: RequestFactory) -> None:
        """An existing session slug is returned verbatim."""
        request = _make_request_with_session(
            rf,
            session={SESSION_EVENT_SLUG_KEY: "pyconde-2026"},
        )
        assert get_selected_event_slug(request) == "pyconde-2026"


class TestSetSelectedEventSlug:
    """Tests for ``set_selected_event_slug``."""

    def test_writes_slug_to_session(self, rf: RequestFactory) -> None:
        """Setting a slug on a writable session stores it under the known key."""
        session: dict[str, Any] = {}
        request = _make_request_with_session(rf, session=session)
        set_selected_event_slug(request, "pyconde-2026")
        assert session == {SESSION_EVENT_SLUG_KEY: "pyconde-2026"}

    def test_no_op_without_session(self, rf: RequestFactory) -> None:
        """Requests without a session do not raise; the call is a silent no-op."""
        request = rf.get("/")  # no .session
        # Should not raise.
        set_selected_event_slug(request, "pyconde-2026")


@pytest.mark.django_db
class TestResolveDefaultEvent:
    """Tests for ``resolve_default_event``."""

    def test_prefers_session_slug(self, rf: RequestFactory) -> None:
        """Session slug wins over the DEFAULT_EVENT setting and generic fallback."""
        session_event = baker.make(Event, slug="from-session", is_active=True)
        baker.make(Event, slug="from-setting", is_active=True)

        request = _make_request_with_session(
            rf,
            session={SESSION_EVENT_SLUG_KEY: session_event.slug},
        )
        with override_settings(DEFAULT_EVENT="from-setting"):
            assert resolve_default_event(request) == session_event

    def test_falls_back_to_default_event_setting(self, rf: RequestFactory) -> None:
        """When the session slug is missing, the DEFAULT_EVENT setting is used."""
        baker.make(Event, slug="other", is_active=True)
        setting_event = baker.make(Event, slug="from-setting", is_active=True)

        request = _make_request_with_session(rf)
        with override_settings(DEFAULT_EVENT="from-setting"):
            assert resolve_default_event(request) == setting_event

    def test_falls_back_to_first_active_event(self, rf: RequestFactory) -> None:
        """With neither a session slug nor a matching setting, any active event will do."""
        baker.make(Event, slug="inactive", is_active=False)
        active = baker.make(Event, slug="active", is_active=True)

        request = _make_request_with_session(rf)
        with override_settings(DEFAULT_EVENT=""):
            assert resolve_default_event(request) == active

    def test_inactive_session_event_skipped(self, rf: RequestFactory) -> None:
        """An inactive event stored in the session is ignored (is_active=False marks it dead)."""
        baker.make(Event, slug="inactive", is_active=False)
        active = baker.make(Event, slug="active", is_active=True)

        request = _make_request_with_session(
            rf,
            session={SESSION_EVENT_SLUG_KEY: "inactive"},
        )
        with override_settings(DEFAULT_EVENT=""):
            assert resolve_default_event(request) == active

    def test_returns_none_when_no_active_events(self, rf: RequestFactory) -> None:
        """With no active events at all, the function returns ``None``."""
        baker.make(Event, slug="inactive", is_active=False)
        request = _make_request_with_session(rf)
        with override_settings(DEFAULT_EVENT=""):
            assert resolve_default_event(request) is None
