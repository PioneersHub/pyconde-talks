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


# ---------------------------------------------------------------------------
# OAuth2 Bearer token tests
# ---------------------------------------------------------------------------


@pytest.fixture()
def oauth2_settings(settings: SettingsWrapper) -> dict[str, str]:
    """Configure OAuth2 client-credentials settings and return the URLs."""
    token_url = "https://keycloak.example.com/realms/test/protocol/openid-connect/token"
    api_url = "https://fake-api.example.com/validate"
    settings.EMAIL_VALIDATION_API_OAUTH2_CLIENT_ID = "test-client"
    settings.EMAIL_VALIDATION_API_OAUTH2_CLIENT_SECRET = "test-secret"
    settings.EMAIL_VALIDATION_API_OAUTH2_TOKEN_URL = token_url
    settings.EMAIL_VALIDATION_API_URL_FALLBACK = api_url
    settings.EMAIL_VALIDATION_API_TIMEOUT = 1
    settings.AUTHORIZED_EMAILS_WHITELIST = []
    return {"token_url": token_url, "api_url": api_url}


@pytest.mark.django_db
def test_oauth2_bearer_token_sent(
    adapter: AccountAdapter,
    oauth2_settings: dict[str, str],
    respx_mock: respx.MockRouter,
) -> None:
    """When OAuth2 is configured, the validation API call includes a Bearer token."""
    token_url = oauth2_settings["token_url"]
    api_url = oauth2_settings["api_url"]

    respx_mock.post(token_url).mock(
        return_value=httpx.Response(200, json={"access_token": "tok-123", "expires_in": 300}),
    )
    respx_mock.post(api_url).mock(
        return_value=httpx.Response(200, json={"valid": True}),
    )

    assert adapter.is_email_authorized("user@example.com") is True

    # Token endpoint called once
    token_calls = [c for c in respx_mock.calls if str(c.request.url) == token_url]
    assert len(token_calls) == 1

    # Validation API called with Bearer header
    api_calls = [c for c in respx_mock.calls if str(c.request.url) == api_url]
    assert len(api_calls) == 1
    assert api_calls[0].request.headers["Authorization"] == "Bearer tok-123"


@pytest.mark.django_db
def test_oauth2_disabled_no_auth_header(
    adapter: AccountAdapter,
    settings: SettingsWrapper,
    respx_mock: respx.MockRouter,
) -> None:
    """When OAuth2 settings are empty, no Authorization header is sent."""
    api_url = "https://fake-api.example.com/validate"
    settings.EMAIL_VALIDATION_API_URL_FALLBACK = api_url
    settings.EMAIL_VALIDATION_API_TIMEOUT = 1
    settings.AUTHORIZED_EMAILS_WHITELIST = []
    settings.EMAIL_VALIDATION_API_OAUTH2_CLIENT_ID = ""
    settings.EMAIL_VALIDATION_API_OAUTH2_CLIENT_SECRET = ""
    settings.EMAIL_VALIDATION_API_OAUTH2_TOKEN_URL = ""

    respx_mock.post(api_url).mock(
        return_value=httpx.Response(200, json={"valid": True}),
    )

    assert adapter.is_email_authorized("user@example.com") is True
    assert respx_mock.calls.call_count == 1
    assert "Authorization" not in respx_mock.calls[0].request.headers


@pytest.mark.django_db
def test_oauth2_token_cached_across_calls(
    adapter: AccountAdapter,
    oauth2_settings: dict[str, str],
    respx_mock: respx.MockRouter,
) -> None:
    """The OAuth2 token is fetched once and reused for subsequent validation calls."""
    token_url = oauth2_settings["token_url"]
    api_url = oauth2_settings["api_url"]

    respx_mock.post(token_url).mock(
        return_value=httpx.Response(200, json={"access_token": "cached-tok", "expires_in": 300}),
    )
    respx_mock.post(api_url).mock(
        return_value=httpx.Response(200, json={"valid": True}),
    )

    assert adapter.is_email_authorized("a@example.com") is True
    assert adapter.is_email_authorized("b@example.com") is True

    token_calls = [c for c in respx_mock.calls if str(c.request.url) == token_url]
    assert len(token_calls) == 1  # Only one token fetch


@pytest.mark.django_db
def test_oauth2_token_fetch_failure_propagates(
    adapter: AccountAdapter,
    oauth2_settings: dict[str, str],
    respx_mock: respx.MockRouter,
) -> None:
    """When the token endpoint fails, the validation call fails gracefully."""
    token_url = oauth2_settings["token_url"]
    api_url = oauth2_settings["api_url"]

    respx_mock.post(token_url).mock(
        return_value=httpx.Response(401, json={"error": "invalid_client"}),
    )
    respx_mock.post(api_url).mock(
        return_value=httpx.Response(200, json={"valid": True}),
    )

    assert adapter.is_email_authorized("user@example.com") is False

    # Validation API should not have been called
    api_calls = [c for c in respx_mock.calls if str(c.request.url) == api_url]
    assert len(api_calls) == 0
