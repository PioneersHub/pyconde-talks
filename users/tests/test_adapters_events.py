"""Tests for event-aware email authorization in the custom adapter."""

from typing import TYPE_CHECKING, Any

import httpx
import pytest

from events.models import Event
from users.adapters import AccountAdapter


if TYPE_CHECKING:
    import respx
    from pytest_django.fixtures import SettingsWrapper


@pytest.fixture()
def adapter() -> AccountAdapter:
    """Return an AccountAdapter instance."""
    return AccountAdapter()


@pytest.fixture()
def event_with_api() -> Event:
    """Return an active Event with a validation API URL."""
    return Event.objects.create(
        name="Event With API",
        slug="event-api",
        year=2025,
        validation_api_url="https://event-api.example.com/validate",
        is_active=True,
    )


@pytest.fixture()
def event_without_api() -> Event:
    """Return an active Event without a validation API URL."""
    return Event.objects.create(
        name="Event No API",
        slug="event-no-api",
        year=2025,
        validation_api_url="",
        is_active=True,
    )


@pytest.mark.django_db
class TestSetSelectedEvent:
    """Tests for the set_selected_event method."""

    def test_set_event(self, adapter: AccountAdapter, event_with_api: Event) -> None:
        """Setting the selected event stores it on the adapter."""
        adapter.set_selected_event(event_with_api)
        assert adapter._selected_event == event_with_api  # noqa: SLF001

    def test_set_none(self, adapter: AccountAdapter) -> None:
        """Setting None clears the selected event."""
        adapter.set_selected_event(None)
        assert adapter._selected_event is None  # noqa: SLF001


@pytest.mark.django_db
class TestEventAwareAuthorization:
    """Tests for is_email_authorized with event-aware logic."""

    def test_existing_user_linked_to_event_authorized(
        self,
        adapter: AccountAdapter,
        user_model: type[Any],
        event_with_api: Event,
        settings: SettingsWrapper,
    ) -> None:
        """User already linked to the selected event is authorized immediately."""
        settings.AUTHORIZED_EMAILS_WHITELIST = []
        settings.EMAIL_VALIDATION_API_URL_FALLBACK = ""
        user = user_model.objects.create_user(email="linked@example.com")
        user.events.add(event_with_api)

        adapter.set_selected_event(event_with_api)
        assert adapter.is_email_authorized("linked@example.com") is True

    def test_existing_user_not_linked_no_api_denied(
        self,
        adapter: AccountAdapter,
        user_model: type[Any],
        event_without_api: Event,
        settings: SettingsWrapper,
    ) -> None:
        """User NOT linked to event, no API configured -> denied."""
        settings.AUTHORIZED_EMAILS_WHITELIST = []
        settings.EMAIL_VALIDATION_API_URL_FALLBACK = ""
        user_model.objects.create_user(email="unlinked@example.com")

        adapter.set_selected_event(event_without_api)
        assert adapter.is_email_authorized("unlinked@example.com") is False

    def test_existing_user_not_linked_api_valid_links_user(
        self,
        adapter: AccountAdapter,
        user_model: type[Any],
        event_with_api: Event,
        settings: SettingsWrapper,
        respx_mock: respx.MockRouter,
    ) -> None:
        """User NOT linked, event API validates -> authorized AND linked to event."""
        settings.AUTHORIZED_EMAILS_WHITELIST = []
        settings.EMAIL_VALIDATION_API_URL_FALLBACK = ""
        user = user_model.objects.create_user(email="newticket@example.com")

        respx_mock.post(event_with_api.validation_api_url).mock(
            return_value=httpx.Response(200, json={"valid": True}),
        )

        adapter.set_selected_event(event_with_api)
        assert adapter.is_email_authorized("newticket@example.com") is True
        # User should now be associated with the event
        assert user.events.filter(pk=event_with_api.pk).exists()

    def test_existing_user_not_linked_api_invalid_denied(
        self,
        adapter: AccountAdapter,
        user_model: type[Any],
        event_with_api: Event,
        settings: SettingsWrapper,
        respx_mock: respx.MockRouter,
    ) -> None:
        """User NOT linked, event API rejects -> denied, NOT linked."""
        settings.AUTHORIZED_EMAILS_WHITELIST = []
        settings.EMAIL_VALIDATION_API_URL_FALLBACK = ""
        user = user_model.objects.create_user(email="rejected@example.com")

        respx_mock.post(event_with_api.validation_api_url).mock(
            return_value=httpx.Response(200, json={"valid": False}),
        )

        adapter.set_selected_event(event_with_api)
        assert adapter.is_email_authorized("rejected@example.com") is False
        assert not user.events.filter(pk=event_with_api.pk).exists()

    def test_new_user_api_valid(
        self,
        adapter: AccountAdapter,
        event_with_api: Event,
        settings: SettingsWrapper,
        respx_mock: respx.MockRouter,
    ) -> None:
        """Non-existent user, event API validates -> authorized (user created later)."""
        settings.AUTHORIZED_EMAILS_WHITELIST = []
        settings.EMAIL_VALIDATION_API_URL_FALLBACK = ""

        respx_mock.post(event_with_api.validation_api_url).mock(
            return_value=httpx.Response(200, json={"valid": True}),
        )

        adapter.set_selected_event(event_with_api)
        assert adapter.is_email_authorized("brand-new@example.com") is True

    def test_event_api_url_takes_precedence_over_global(
        self,
        adapter: AccountAdapter,
        event_with_api: Event,
        settings: SettingsWrapper,
        respx_mock: respx.MockRouter,
    ) -> None:
        """Event-specific API URL is used instead of the global fallback."""
        global_url = "https://global-api.example.com/validate"
        settings.AUTHORIZED_EMAILS_WHITELIST = []
        settings.EMAIL_VALIDATION_API_URL_FALLBACK = global_url

        respx_mock.post(event_with_api.validation_api_url).mock(
            return_value=httpx.Response(200, json={"valid": True}),
        )

        adapter.set_selected_event(event_with_api)
        assert adapter.is_email_authorized("user@example.com") is True
        # Should have called the event API, not the global one
        assert respx_mock.calls.call_count == 1
        assert str(respx_mock.calls[0].request.url) == event_with_api.validation_api_url

    def test_global_api_fallback_when_event_has_no_url(
        self,
        adapter: AccountAdapter,
        event_without_api: Event,
        settings: SettingsWrapper,
        respx_mock: respx.MockRouter,
    ) -> None:
        """When event has no API URL, global fallback is used."""
        global_url = "https://global-api.example.com/validate"
        settings.AUTHORIZED_EMAILS_WHITELIST = []
        settings.EMAIL_VALIDATION_API_URL_FALLBACK = global_url

        respx_mock.post(global_url).mock(
            return_value=httpx.Response(200, json={"valid": True}),
        )

        adapter.set_selected_event(event_without_api)
        assert adapter.is_email_authorized("fallback@example.com") is True
        assert respx_mock.calls.call_count == 1
        assert str(respx_mock.calls[0].request.url) == global_url

    def test_no_event_no_api_denies(
        self,
        adapter: AccountAdapter,
        settings: SettingsWrapper,
    ) -> None:
        """No event selected and no global API -> denied."""
        settings.AUTHORIZED_EMAILS_WHITELIST = []
        settings.EMAIL_VALIDATION_API_URL_FALLBACK = ""

        adapter.set_selected_event(None)
        assert adapter.is_email_authorized("nobody@example.com") is False

    def test_superuser_bypasses_event_check(
        self,
        adapter: AccountAdapter,
        user_model: type[Any],
        event_with_api: Event,
        settings: SettingsWrapper,
    ) -> None:
        """Superusers are always authorized, regardless of event association."""
        settings.AUTHORIZED_EMAILS_WHITELIST = []
        settings.EMAIL_VALIDATION_API_URL_FALLBACK = ""
        user_model.objects.create_superuser(
            email="super@example.com",
            password="password",
        )
        adapter.set_selected_event(event_with_api)
        assert adapter.is_email_authorized("super@example.com") is True
