"""Tests for the custom allauth adapter that validates emails using an external API."""

import json
from typing import Any

import pytest
import responses
from django.conf import settings
from pytest_django.fixtures import SettingsWrapper

from users.adapters import AccountAdapter


@pytest.fixture()
def adapter() -> AccountAdapter:
    """Return an instance of the AccountAdapter for testing."""
    return AccountAdapter()


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
    settings: SettingsWrapper,
    mock_email_api_invalid: None,
) -> None:
    """
    Test email authorization for superusers.

    Ensures that superuser emails are authorized regardless of whitelist status.
    """
    # Create a superuser
    user_model.objects.create_superuser(
        email="admin@example.com",
        password="password",
    )

    # Create a regular user
    user_model.objects.create_user(
        email="user@example.com",
    )

    # Empty the whitelist to ensure we're testing the superuser check
    settings.AUTHORIZED_EMAILS_WHITELIST = []

    # Test superuser email is authorized
    assert adapter.is_email_authorized("admin@example.com") is True

    # Test regular user email is not automatically authorized
    assert adapter.is_email_authorized("user@example.com") is False

    # Test non-existent user email
    assert adapter.is_email_authorized("nonexistent@example.com") is False


@pytest.mark.django_db
@responses.activate
def test_api_authorization_success(
    adapter: AccountAdapter,
    settings: SettingsWrapper,
    mock_email_api_valid: str,
) -> None:
    """Test successful email authorization via the external API."""
    # Make sure we're testing the API path by clearing the whitelist
    settings.AUTHORIZED_EMAILS_WHITELIST = []

    # Test authorization - should succeed because we've mocked the API to return valid=True
    assert adapter.is_email_authorized("user@example.com") is True

    # Check request was properly formed
    assert len(responses.calls) == 1
    assert json.loads(responses.calls[0].request.body) == {"email": "user@example.com"}


@pytest.mark.django_db
@responses.activate
def test_api_authorization_failure(
    adapter: AccountAdapter,
    settings: SettingsWrapper,
    mock_email_api_invalid: str,
) -> None:
    """Test failed email authorization via the external API."""
    # Make sure we're testing the API path by clearing the whitelist
    settings.AUTHORIZED_EMAILS_WHITELIST = []

    # Test authorization - should fail because we've mocked the API to return valid=False
    assert adapter.is_email_authorized("user@example.com") is False

    # Check request was properly formed
    assert len(responses.calls) == 1
    assert json.loads(responses.calls[0].request.body) == {"email": "user@example.com"}


@pytest.mark.django_db
@responses.activate
def test_api_authorization_validation_error(
    adapter: AccountAdapter,
    settings: SettingsWrapper,
    mock_email_api_error: str,
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
    assert len(responses.calls) == 1
    assert json.loads(responses.calls[0].request.body) == {"email": test_email}
