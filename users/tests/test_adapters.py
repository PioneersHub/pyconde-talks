"""Tests for the custom allauth adapter that validates emails using an external API."""

from typing import Any

import pytest
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
