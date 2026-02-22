"""Tests for event-related logic in the user views (login flow)."""
# ruff: noqa: ARG002

from typing import TYPE_CHECKING, Any

import pytest
from django.http import HttpResponse, HttpResponseRedirect
from django.test import RequestFactory
from django.urls import reverse

from events.models import Event
from users.views import CustomRequestLoginCodeView


if TYPE_CHECKING:
    from django.test.client import Client
    from pytest_mock import MockerFixture


@pytest.fixture()
def request_factory() -> RequestFactory:
    """Return a RequestFactory instance."""
    return RequestFactory()


@pytest.fixture()
def view() -> CustomRequestLoginCodeView:
    """Return a CustomRequestLoginCodeView instance."""
    return CustomRequestLoginCodeView()


@pytest.fixture()
def event() -> Event:
    """Create an active test event."""
    return Event.objects.create(
        name="Test Event 2025",
        slug="test-2025",
        year=2025,
        is_active=True,
    )


@pytest.mark.django_db
class TestFormValidEventSelection:
    """Verify event selection in the login form_valid flow."""

    def test_event_resolved_from_post_data(
        self,
        request_factory: RequestFactory,
        view: CustomRequestLoginCodeView,
        event: Event,
        mocker: MockerFixture,
        allauth_settings: None,
    ) -> None:
        """form_valid resolves event from POST data and sets it on adapter."""
        form = mocker.MagicMock()
        form.is_valid.return_value = True
        form.cleaned_data = {"email": "user@example.com"}

        # POST with event slug
        request = request_factory.post(
            reverse("account_login"),
            {"email": "user@example.com", "event": event.slug},
        )
        view.request = request

        mock_adapter = mocker.MagicMock()
        mock_adapter.is_email_authorized.return_value = True
        mocker.patch("users.views.get_adapter", return_value=mock_adapter)

        mocker.patch.object(
            view.__class__.__bases__[0],
            "form_valid",
            return_value=HttpResponse("success"),
        )

        view.form_valid(form)

        # Adapter should have been called with the event
        mock_adapter.set_selected_event.assert_called_once_with(event)

    def test_inactive_event_resolves_to_none(
        self,
        request_factory: RequestFactory,
        view: CustomRequestLoginCodeView,
        mocker: MockerFixture,
        allauth_settings: None,
    ) -> None:
        """Inactive event slug resolves to None."""
        Event.objects.create(name="Old", slug="old-event", year=2025, is_active=False)

        form = mocker.MagicMock()
        form.is_valid.return_value = True
        form.cleaned_data = {"email": "user@example.com"}

        request = request_factory.post(
            reverse("account_login"),
            {"email": "user@example.com", "event": "old-event"},
        )
        view.request = request

        mock_adapter = mocker.MagicMock()
        mock_adapter.is_email_authorized.return_value = True
        mocker.patch("users.views.get_adapter", return_value=mock_adapter)

        mocker.patch.object(
            view.__class__.__bases__[0],
            "form_valid",
            return_value=HttpResponse("success"),
        )

        view.form_valid(form)
        mock_adapter.set_selected_event.assert_called_once_with(None)

    def test_new_user_linked_to_event(  # noqa: PLR0913
        self,
        request_factory: RequestFactory,
        view: CustomRequestLoginCodeView,
        event: Event,
        mocker: MockerFixture,
        allauth_settings: None,
        user_model: type[Any],
    ) -> None:
        """New user created during login is associated with the selected event."""
        form = mocker.MagicMock()
        form.is_valid.return_value = True
        form.cleaned_data = {"email": "newuser@example.com"}

        request = request_factory.post(
            reverse("account_login"),
            {"email": "newuser@example.com", "event": event.slug},
        )
        view.request = request

        mock_adapter = mocker.MagicMock()
        mock_adapter.is_email_authorized.return_value = True
        mocker.patch("users.views.get_adapter", return_value=mock_adapter)

        # Mock the login code flow so we don't actually send emails
        mocker.patch("users.views.flows.login_by_code.LoginCodeVerificationProcess.initiate")
        mocker.patch.object(view, "get_success_url", return_value="/success/")

        result = view.form_valid(form)

        # User should have been created and linked to the event
        user = user_model.objects.get(email="newuser@example.com")
        assert user.events.filter(pk=event.pk).exists()
        assert isinstance(result, HttpResponseRedirect)


@pytest.mark.django_db
class TestGetContextDataEvents:
    """Verify get_context_data provides event context."""

    def test_context_includes_active_events(self, client: Client) -> None:
        """Context includes only active events."""
        Event.objects.create(name="Active", slug="active-ctx", year=2025, is_active=True)
        Event.objects.create(
            name="Inactive",
            slug="inactive-ctx",
            year=2025,
            is_active=False,
        )

        response = client.get(reverse("account_login"))
        events = list(response.context["events"])
        slugs = [e.slug for e in events]
        assert "active-ctx" in slugs
        assert "inactive-ctx" not in slugs

    def test_context_includes_default_event_slug(
        self,
        client: Client,
        settings: object,
    ) -> None:
        """Context includes DEFAULT_EVENT from settings."""
        settings.DEFAULT_EVENT = "test-default-2025"  # type: ignore[attr-defined]
        response = client.get(reverse("account_login"))
        assert response.context["default_event_slug"] == "test-default-2025"
