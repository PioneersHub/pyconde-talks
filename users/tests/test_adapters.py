"""Tests for the custom allauth adapter that validates emails using an external API."""

import pytest
from django.conf import settings

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
