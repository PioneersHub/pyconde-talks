"""Shared test fixtures for the users app."""

from typing import TYPE_CHECKING, Any

import httpx
import pytest
from django.contrib.auth import get_user_model


if TYPE_CHECKING:
    import respx
    from pytest_django.fixtures import SettingsWrapper


@pytest.fixture()
def user_model() -> type[Any]:
    """Return the user model being used by the application."""
    return get_user_model()


@pytest.fixture()
def mock_email_api_base(settings: SettingsWrapper) -> str:
    """
    Define base fixture that sets up the mock email validation API infrastructure.

    This fixture configures settings but doesn't add any response mocks.
    It's not meant to be used directly by tests.

    Args:
        settings: Django settings fixture

    Returns:
        str: The fake API URL for use by derived fixtures

    """
    fake_api_url = "https://fake-api.example.com/validate"
    settings.EMAIL_VALIDATION_API_URL_FALLBACK = fake_api_url
    settings.EMAIL_VALIDATION_API_TIMEOUT = 1

    return fake_api_url


@pytest.fixture()
def mock_email_api_valid(mock_email_api_base: str, respx_mock: respx.MockRouter) -> str:
    """
    Mock the email validation API to return valid=True for all emails.

    Args:
        mock_email_api_base: The base fixture that sets up infrastructure
        respx_mock: The respx mock router fixture

    Returns:
        str: The fake API URL

    """
    api_url = mock_email_api_base

    respx_mock.post(api_url).mock(
        return_value=httpx.Response(200, json={"valid": True}),
    )

    return api_url


@pytest.fixture()
def mock_email_api_invalid(mock_email_api_base: str, respx_mock: respx.MockRouter) -> str:
    """
    Mock the email validation API to return valid=False for all emails.

    Args:
        mock_email_api_base: The base fixture that sets up infrastructure
        respx_mock: The respx mock router fixture

    Returns:
        str: The fake API URL

    """
    api_url = mock_email_api_base

    respx_mock.post(api_url).mock(
        return_value=httpx.Response(404, json={"valid": False}),
    )

    return api_url


@pytest.fixture()
def mock_email_api_error(mock_email_api_base: str, respx_mock: respx.MockRouter) -> str:
    """
    Mock the email validation API to return a 422 validation error.

    This simulates the API rejecting the request due to an invalid email format.

    Args:
        mock_email_api_base: The base fixture that sets up infrastructure
        respx_mock: The respx mock router fixture

    Returns:
        str: The fake API URL

    """
    api_url = mock_email_api_base

    respx_mock.post(api_url).mock(
        return_value=httpx.Response(
            422,
            json={
                "detail": [
                    {
                        "type": "value_error",
                        "loc": [
                            "body",
                            "email",
                        ],
                        "msg": (
                            "value is not a valid email address:"
                            " An email address must have an @-sign."
                        ),
                        "input": "invalid-email-format",
                        "ctx": {
                            "reason": "An email address must have an @-sign.",
                        },
                    },
                ],
            },
        ),
    )

    return api_url


@pytest.fixture()
def mock_email_api_exception(mock_email_api_base: str, respx_mock: respx.MockRouter) -> str:
    """
    Mock the email validation API to raise an exception during the request.

    This simulates network errors, timeouts, or other connection problems.

    Args:
        mock_email_api_base: The base fixture that sets up infrastructure
        respx_mock: The respx mock router fixture

    Returns:
        str: The fake API URL

    """
    api_url = mock_email_api_base

    respx_mock.post(api_url).mock(side_effect=httpx.ConnectError("Connection error"))

    return api_url


@pytest.fixture()
def allauth_settings(settings: SettingsWrapper) -> None:
    """Configure Allauth settings for passwordless login."""
    settings.ACCOUNT_ADAPTER = "users.adapters.AccountAdapter"
    settings.ACCOUNT_EMAIL_REQUIRED = True
    settings.ACCOUNT_EMAIL_UNKNOWN_ACCOUNTS = False
    settings.ACCOUNT_USERNAME_REQUIRED = False
    settings.ACCOUNT_AUTHENTICATION_METHOD = "email"
    settings.ACCOUNT_EMAIL_VERIFICATION = "mandatory"
    settings.ACCOUNT_LOGIN_BY_CODE_ENABLED = True
    settings.ACCOUNT_LOGIN_BY_CODE_TIMEOUT = 180
    settings.ACCOUNT_LOGIN_BY_CODE_MAX_ATTEMPTS = 3
    settings.ACCOUNT_PREVENT_ENUMERATION = True
