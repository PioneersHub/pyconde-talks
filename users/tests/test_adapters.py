"""Tests for the custom allauth adapter that validates emails using an external API."""

import json
from typing import TYPE_CHECKING, Any

import httpx
import pytest
from django.conf import settings

from events.models import Event
from users.adapters import AccountAdapter


if TYPE_CHECKING:
    import respx
    from pytest_django.fixtures import SettingsWrapper


@pytest.fixture()
def adapter() -> AccountAdapter:
    """Return an instance of the AccountAdapter for testing."""
    return AccountAdapter()


@pytest.fixture()
def event() -> Event:
    """Return an active Event for testing."""
    return Event.objects.create(
        name="Test Event 2025",
        slug="test-event-2025",
        year=2025,
        validation_api_url="",
        is_active=True,
    )


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("whitelist", "email", "expected"),
    [
        (["test@example.com"], "test@example.com", True),
        (["test@example.com"], "other@example.com", False),
        ([], "test@example.com", False),
        (["test@example.com"], "Test@example.com", True),  # Case-insensitivity
        (["test@example.com"], " test@example.com", True),  # Whitespace trimming
    ],
)
def test_whitelist_authorization(
    adapter: AccountAdapter,
    monkeypatch: pytest.MonkeyPatch,
    whitelist: list[str],
    email: str,
    *,
    expected: bool,
) -> None:
    """Test email authorization using the whitelist."""
    monkeypatch.setattr(settings, "AUTHORIZED_EMAILS_WHITELIST", whitelist)
    assert adapter.is_email_authorized(email) == expected


@pytest.mark.django_db
def test_superuser_authorization(
    adapter: AccountAdapter,
    user_model: type[Any],
    event: Event,
    settings: SettingsWrapper,
) -> None:
    """
    Test email authorization for superusers and event-associated users.

    Ensures that superuser emails are authorized regardless of event association,
    and that regular users are authorized only when associated with the selected event.
    """
    # Create a superuser
    user_model.objects.create_superuser(
        email="admin@example.com",
        password="password",
    )

    # Create a regular user associated with the event
    user_with_event = user_model.objects.create_user(email="user@example.com")
    user_with_event.events.add(event)

    # Create a regular user NOT associated with the event
    user_model.objects.create_user(email="noticket@example.com")

    # Empty the whitelist and disable API to ensure we're testing local checks only
    settings.AUTHORIZED_EMAILS_WHITELIST = []
    settings.EMAIL_VALIDATION_API_URL_FALLBACK = ""

    # Tell the adapter which event the user is logging in for
    adapter.set_selected_event(event)

    # Test superuser email is authorized (regardless of event association)
    assert adapter.is_email_authorized("admin@example.com") is True

    # Test regular user associated with the event is authorized
    assert adapter.is_email_authorized("user@example.com") is True

    # Test regular user NOT associated with the event is denied (no API configured)
    assert adapter.is_email_authorized("noticket@example.com") is False

    # Test non-existent user email
    assert adapter.is_email_authorized("nonexistent@example.com") is False


@pytest.mark.django_db
def test_api_authorization_success(
    adapter: AccountAdapter,
    settings: SettingsWrapper,
    mock_email_api_valid: str,
    respx_mock: respx.MockRouter,
) -> None:
    """Test successful email authorization via the external API."""
    # Make sure we're testing the API path by clearing the whitelist
    settings.AUTHORIZED_EMAILS_WHITELIST = []

    # Test authorization - should succeed because we've mocked the API to return valid=True
    assert adapter.is_email_authorized("user@example.com") is True

    # Check request was properly formed
    assert respx_mock.calls.call_count == 1
    request = respx_mock.calls[0].request
    assert json.loads(request.content) == {"email": "user@example.com"}


@pytest.mark.django_db
def test_api_authorization_failure(
    adapter: AccountAdapter,
    settings: SettingsWrapper,
    mock_email_api_invalid: str,
    respx_mock: respx.MockRouter,
) -> None:
    """Test failed email authorization via the external API."""
    # Make sure we're testing the API path by clearing the whitelist
    settings.AUTHORIZED_EMAILS_WHITELIST = []

    # Test authorization - should fail because we've mocked the API to return valid=False
    assert adapter.is_email_authorized("user@example.com") is False

    # Check request was properly formed
    assert respx_mock.calls.call_count == 1
    request = respx_mock.calls[0].request
    assert json.loads(request.content) == {"email": "user@example.com"}


@pytest.mark.django_db
def test_api_authorization_validation_error(
    adapter: AccountAdapter,
    settings: SettingsWrapper,
    mock_email_api_error: str,
    respx_mock: respx.MockRouter,
) -> None:
    """
    Test API authorization with validation error response.

    Verifies that authorization fails gracefully when the API rejects the request due to email
    format validation errors.
    """
    # Clear the whitelist to ensure we test the API path
    settings.AUTHORIZED_EMAILS_WHITELIST = []

    # Test with a malformed email (without @)
    test_email = "invalid-email-format"

    # Test that authorization fails gracefully, even with a 422 response
    assert adapter.is_email_authorized(test_email) is False

    # Verify the request was made
    assert respx_mock.calls.call_count == 1
    request = respx_mock.calls[0].request
    assert json.loads(request.content) == {"email": test_email}


@pytest.mark.django_db
def test_api_authorization_exceptions(
    adapter: AccountAdapter,
    settings: SettingsWrapper,
    mock_email_api_exception: str,
    respx_mock: respx.MockRouter,
) -> None:
    """
    Test API authorization with exceptions during request.

    Verifies that authorization fails gracefully when API request raises exceptions.
    """
    # Clear the whitelist to ensure we test the API path
    settings.AUTHORIZED_EMAILS_WHITELIST = []

    # Test that authorization fails gracefully when the API raises an exception
    assert adapter.is_email_authorized("error@example.com") is False

    # Verify that a request attempt was made
    assert respx_mock.calls.call_count >= 1
    assert str(respx_mock.calls[0].request.url) == mock_email_api_exception


@pytest.mark.django_db
class TestSendMail:
    """Tests for AccountAdapter.send_mail context injection."""

    def test_send_mail_adds_context(
        self,
        adapter: AccountAdapter,
        mocker: Any,
        settings: Any,
    ) -> None:
        """send_mail injects branding and timeout context values."""
        settings.ACCOUNT_LOGIN_BY_CODE_TIMEOUT = 300  # 5 minutes

        event = Event.objects.create(
            name="PyCon DE 2099",
            slug="pyconde-2099",
            year=2099,
            is_active=True,
        )
        adapter.set_selected_event(event)

        mock_super = mocker.patch(
            "allauth.account.adapter.DefaultAccountAdapter.send_mail",
        )

        adapter.send_mail("account/email/login_code", "user@test.com", {"key": "value"})

        mock_super.assert_called_once()
        ctx = mock_super.call_args[0][2]
        expected_timeout_minutes = 300 // 60
        assert ctx["login_code_timeout_minutes"] == expected_timeout_minutes
        assert ctx["brand_event_name"] == "PyCon DE 2099"
        assert ctx["brand_event_year"] == "2099"
        assert ctx["brand_title"] == "PyCon DE 2099 Talks"


@pytest.mark.django_db
def test_api_authorization_valid_false(
    adapter: AccountAdapter,
    settings: SettingsWrapper,
    respx_mock: respx.MockRouter,
) -> None:
    """Test API returns valid=false — hits the warning branch."""
    api_url = "https://fake-api.example.com/validate"
    settings.EMAIL_VALIDATION_API_URL_FALLBACK = api_url
    settings.AUTHORIZED_EMAILS_WHITELIST = []

    respx_mock.post(api_url).mock(
        return_value=httpx.Response(200, json={"valid": False}),
    )

    assert adapter.is_email_authorized("rejected@example.com") is False
    assert respx_mock.calls.call_count == 1
