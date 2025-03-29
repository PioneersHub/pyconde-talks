"""Tests for the custom authentication views."""

from typing import Any

import pytest
from django.test import RequestFactory
from django.urls import reverse
from pytest_mock import MockerFixture

from users.views import CustomRequestLoginCodeView


@pytest.fixture()
def request_factory() -> RequestFactory:
    """Return a RequestFactory instance for creating test requests."""
    return RequestFactory()


@pytest.fixture()
def login_form_data() -> dict[str, str]:
    """Return test data for the login form with email only (for passwordless login)."""
    return {"email": "test@example.com"}


@pytest.fixture()
def view() -> CustomRequestLoginCodeView:
    """Return an instance of the CustomRequestLoginCodeView."""
    return CustomRequestLoginCodeView()


@pytest.mark.django_db
def test_form_valid_authorized_existing_user(  # noqa: PLR0913
    request_factory: RequestFactory,
    login_form_data: dict[str, str],
    view: CustomRequestLoginCodeView,
    mocker: MockerFixture,
    allauth_settings: None,
    user_model: type[Any],
) -> None:
    """
    Test form_valid with an authorized email for an existing user.

    Verifies that when an authorized email belonging to an existing user is submitted, the view
    proceeds with the login code process.
    """
    # Create a user
    user_model.objects.create_user(email=login_form_data["email"])

    # Create form with valid data
    form = mocker.MagicMock()
    form.is_valid.return_value = True
    form.cleaned_data = {"email": login_form_data["email"].lower()}

    # Mock the request
    request = request_factory.post(reverse("account_login"))
    view.request = request

    # Mock the adapter's is_email_authorized method to return True
    mock_adapter = mocker.MagicMock()
    mock_adapter.is_email_authorized.return_value = True

    # Patch get_adapter to return our mock adapter
    mocker.patch("users.views.get_adapter", return_value=mock_adapter)

    # Mock the parent class's form_valid method
    # (because CustomRequestLoginCodeView.form_valid calls super().form_valid)
    mocker.patch.object(
        view.__class__.__bases__[0],
        "form_valid",
        return_value="success",
    )

    # Call our view's form_valid method
    response = view.form_valid(form)

    # Check that the adapter's is_email_authorized was called with the correct email
    mock_adapter.is_email_authorized.assert_called_once_with(login_form_data["email"].lower())

    # Assert that the response is the one from the parent class
    assert response == "success"


@pytest.mark.django_db
def test_form_valid_authorized_new_user(  # noqa: PLR0913
    request_factory: RequestFactory,
    login_form_data: dict[str, str],
    view: CustomRequestLoginCodeView,
    mocker: MockerFixture,
    allauth_settings: None,
    user_model: type[Any],
) -> None:
    """
    Test form_valid with an authorized email for a new user.

    Verifies that when an authorized email for a non-existing user is submitted, a new user is
    created and the view proceeds with the login code process.

    """
    # Create form with valid data
    form = mocker.MagicMock()
    form.is_valid.return_value = True
    form.cleaned_data = {"email": login_form_data["email"].lower()}

    # Mock the request
    request = request_factory.post(reverse("account_login"))
    view.request = request

    # Mock the adapter's is_email_authorized method to return True
    mock_adapter = mocker.MagicMock()
    mock_adapter.is_email_authorized.return_value = True

    # Patch get_adapter to return our mock adapter
    mocker.patch("users.views.get_adapter", return_value=mock_adapter)

    # Mock the parent class's form_valid method
    mocker.patch.object(
        view.__class__.__bases__[0],
        "form_valid",
        return_value="success",
    )

    # Call our view's form_valid method
    response = view.form_valid(form)

    # Check that the adapter's is_email_authorized was called
    mock_adapter.is_email_authorized.assert_called_once_with(login_form_data["email"].lower())

    # Check that a new user was created
    assert user_model.objects.filter(email=login_form_data["email"].lower()).exists()

    # Assert that the response is the one from the parent class
    assert response == "success"


@pytest.mark.django_db
def test_form_valid_unauthorized_email(
    request_factory: RequestFactory,
    login_form_data: dict[str, str],
    view: CustomRequestLoginCodeView,
    mocker: MockerFixture,
    allauth_settings: None,
) -> None:
    """
    Test form_valid with an unauthorized email.

    Verifies that when an unauthorized email is submitted, the view adds an error to the form and
    calls form_invalid.
    """
    # Create form with valid data
    form = mocker.MagicMock()
    form.is_valid.return_value = True
    form.cleaned_data = {"email": login_form_data["email"].lower()}
    form.add_error = mocker.MagicMock()

    # Mock the request
    request = request_factory.post(reverse("account_login"))
    view.request = request

    # Mock the adapter's is_email_authorized method to return False
    mock_adapter = mocker.MagicMock()
    mock_adapter.is_email_authorized.return_value = False

    # Patch get_adapter to return our mock adapter
    mocker.patch("users.views.get_adapter", return_value=mock_adapter)

    # Mock the form_invalid method
    mocker.patch.object(view, "form_invalid", return_value="form_invalid")

    # Call our view's form_valid method
    response = view.form_valid(form)

    # Check that the adapter's is_email_authorized was called
    mock_adapter.is_email_authorized.assert_called_once_with(login_form_data["email"].lower())

    # Check that the form has an error
    form.add_error.assert_called_once_with("email", "This email is not authorized for access.")

    # Assert that form_invalid was called
    assert response == "form_invalid"
