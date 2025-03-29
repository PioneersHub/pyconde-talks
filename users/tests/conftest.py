"""Shared test fixtures for the users app."""

from typing import Any

import pytest
import responses
from django.contrib.auth import get_user_model
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
    settings.EMAIL_VALIDATION_API_URL = fake_api_url
    settings.EMAIL_VALIDATION_API_TIMEOUT = 1

    return fake_api_url


@pytest.fixture()
def mock_email_api_valid(mock_email_api_base: str) -> str:
    """
    Mock the email validation API to return valid=True for all emails.

    Must be used with @responses.activate decorator in the test.

    Args:
        mock_email_api_base: The base fixture that sets up infrastructure

    Returns:
        str: The fake API URL

    """
    api_url = mock_email_api_base

    # Add a response that indicates the email is valid
    responses.add(
        responses.POST,
        api_url,
        json={"valid": True},
        status=200,
    )

    return api_url


@pytest.fixture()
def mock_email_api_invalid(mock_email_api_base: str) -> str:
    """
    Mock the email validation API to return valid=False for all emails.

    Must be used with @responses.activate decorator in the test.

    Args:
        mock_email_api_base: The base fixture that sets up infrastructure

    Returns:
        str: The fake API URL

    """
    api_url = mock_email_api_base

    # Add a response that indicates the email is invalid
    responses.add(
        responses.POST,
        api_url,
        json={"valid": False},
        status=404,
    )

    return api_url


@pytest.fixture()
def mock_email_api_error(mock_email_api_base: str) -> str:
    """
    Mock the email validation API to return a 422 validation error.

    This simulates the API rejecting the request due to an invalid email format.
    Must be used with @responses.activate decorator in the test.

    Args:
        mock_email_api_base: The base fixture that sets up infrastructure

    Returns:
        str: The fake API URL

    """
    api_url = mock_email_api_base

    # Add a response that simulates a FastAPI validation error
    responses.add(
        responses.POST,
        api_url,
        json={
            "detail": [
                {
                    "type": "value_error",
                    "loc": [
                        "body",
                        "email",
                    ],
                    "msg": (
                        "value is not a valid email address: An email address must have an @-sign."
                    ),
                    "input": "invalid-email-format",
                    "ctx": {
                        "reason": "An email address must have an @-sign.",
                    },
                },
            ],
        },
        status=422,
    )

    return api_url


@pytest.fixture()
def mock_email_api_exception(mock_email_api_base: str) -> str:
    """
    Mock the email validation API to raise an exception during the request.

    This simulates network errors, timeouts, or other connection problems.
    Must be used with @responses.activate decorator in the test.

    Args:
        mock_email_api_base: The base fixture that sets up infrastructure

    Returns:
        str: The fake API URL

    """
    api_url = mock_email_api_base

    # Add a response that simulates a connection error or similar exception
    responses.add(
        responses.POST,
        api_url,
        body=Exception("Connection error"),
    )

    return api_url
