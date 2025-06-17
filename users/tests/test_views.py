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
    # Make sure the user is not in the database
    assert not user_model.objects.filter(email=login_form_data["email"].lower()).exists()

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


@pytest.mark.django_db
def test_form_valid_user_creation_error(
    request_factory: RequestFactory,
    login_form_data: dict[str, str],
    view: CustomRequestLoginCodeView,
    mocker: MockerFixture,
    allauth_settings: None,
) -> None:
    """
    Test form_valid when user creation fails.

    Verifies that when an authorized email is submitted but user creation fails, the view adds an
    error to the form and calls form_invalid.
    """
    # Create form with valid data
    form = mocker.MagicMock()
    form.is_valid.return_value = True
    form.cleaned_data = {"email": login_form_data["email"].lower()}
    form.add_error = mocker.MagicMock()

    # Mock the request with all necessary attributes
    request = request_factory.post(reverse("account_login"))
    request.session = {}
    from django.contrib.messages.storage.fallback import FallbackStorage

    messages = FallbackStorage(request)
    request._messages = messages  # noqa: SLF001
    from django.contrib.auth.models import AnonymousUser

    request.user = AnonymousUser()
    view.request = request

    # Mock the adapter
    mock_adapter = mocker.MagicMock()
    mock_adapter.is_email_authorized.return_value = True
    mocker.patch("users.views.get_adapter", return_value=mock_adapter)

    # Make sure the user doesn't exist but creation fails
    mock_filter = mocker.MagicMock()
    mock_filter.exists.return_value = False
    mock_qs = mocker.MagicMock()
    mock_qs.filter.return_value = mock_filter

    mock_user_model = mocker.MagicMock()
    mock_user_model.objects = mock_qs
    mock_user_model.objects.create_user.side_effect = Exception("User creation failed")

    # Patch at the exact point it's used in the view
    mocker.patch("users.views.get_user_model", return_value=mock_user_model)

    # Mock form_invalid to return a predictable value
    mocker.patch.object(view, "form_invalid", return_value="form_invalid")

    # Call the method being tested
    response = view.form_valid(form)

    # Verify error was added to the form
    form.add_error.assert_called_once()
    args = form.add_error.call_args[0]
    assert args[0] == "email"  # First arg should be field name
    assert "Error creating user" in args[1]  # Second arg is error message

    # Verify form_invalid was called
    assert response == "form_invalid"
