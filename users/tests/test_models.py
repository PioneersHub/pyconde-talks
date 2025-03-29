"""Tests for the custom user model and its manager class."""

from typing import Any

import pytest
from django.core.exceptions import ValidationError

from users.models import CustomUser, InvalidEmailError


@pytest.fixture()
def user_data() -> dict[str, str]:
    """Return test data for creating a regular user."""
    return {
        "email": "test@example.com",
    }


@pytest.fixture()
def superuser_data() -> dict[str, Any]:
    """Return test data for creating a superuser."""
    return {
        "email": "admin@example.com",
        "password": "hunter2",
        "is_staff": True,
        "is_superuser": True,
    }


@pytest.mark.django_db
def test_create_user(user_data: dict[str, Any]) -> None:
    """
    Test creating a regular user with the CustomUserManager.

    Verifies that:
    - User is created with the correct email
    - User has no usable password
    - User has default permissions set correctly
    - Email address is verified

    Args:
        user_data: Dictionary containing user creation parameters

    """
    user = CustomUser.objects.create_user(**user_data)
    assert user.email == user_data["email"]
    assert not user.has_usable_password()
    assert user.is_active
    assert not user.is_staff
    assert not user.is_superuser

    # Check if email address was verified
    assert user.emailaddress_set.count() == 1
    email_obj = user.emailaddress_set.first()
    assert email_obj.email == user_data["email"]
    assert email_obj.verified
    assert email_obj.primary


@pytest.mark.django_db
def test_create_superuser(superuser_data: dict[str, Any]) -> None:
    """
    Test creating a superuser with the CustomUserManager.

    Verifies that:
    - Superuser is created with the correct email
    - Superuser has a usable password
    - Superuser has appropriate permissions

    Args:
        superuser_data: Dictionary containing superuser creation parameters

    """
    user = CustomUser.objects.create_superuser(
        email=superuser_data["email"],
        password=superuser_data["password"],
    )
    assert user.email == superuser_data["email"]
    assert user.has_usable_password()
    assert user.check_password(superuser_data["password"])
    assert user.is_active
    assert user.is_staff
    assert user.is_superuser


@pytest.mark.django_db
def test_create_user_normalize_email() -> None:
    """
    Test email normalization when creating a user.

    Verifies that email addresses are normalized to lowercase.
    """
    email = "TEST@Example.COM   "
    user = CustomUser.objects.create_user(email=email)
    assert user.email == "test@example.com"


@pytest.mark.django_db
def test_create_user_invalid_email() -> None:
    """
    Test creating a user with invalid email addresses.

    Verifies that attempting to create a user with an empty or invalid email raises an
    InvalidEmailError.
    """
    with pytest.raises(InvalidEmailError):
        CustomUser.objects.create_user(email="")

    with pytest.raises(InvalidEmailError):
        CustomUser.objects.create_user(email="not-an-email")


@pytest.mark.django_db
def test_create_user_duplicate_email(user_data: dict[str, Any]) -> None:
    """
    Test creating a user with a duplicate email address.

    The duplication is caught by user.full_clean(), which raises a ValidationError.
    CustomUserManager catches this and raises an InvalidEmailError.
    """
    CustomUser.objects.create_user(**user_data)

    with pytest.raises(InvalidEmailError):
        CustomUser.objects.create_user(**user_data)


@pytest.mark.django_db
def test_create_superuser_without_password() -> None:
    """
    Test creating a superuser without a password.

    Verifies that attempting to create a superuser without providing a password raises a ValueError.
    """
    with pytest.raises(ValueError, match="Superuser must have a password"):
        CustomUser.objects.create_superuser(
            email="admin@example.com",
            password=None,
        )


@pytest.mark.django_db
def test_create_superuser_without_staff_flag() -> None:
    """
    Test creating a superuser without the staff flag.

    Verifies that attempting to create a superuser with is_staff=False raises a ValidationError.
    """
    with pytest.raises(ValidationError, match="Superuser must have is_staff=True"):
        CustomUser.objects.create_superuser(
            email="admin@example.com",
            password="password",
            is_staff=False,
        )


@pytest.mark.django_db
def test_create_superuser_without_superuser_flag() -> None:
    """
    Test creating a superuser without the superuser flag.

    Verifies that attempting to create a superuser with is_superuser=False raises a ValidationError.
    """
    with pytest.raises(ValidationError, match="Superuser must have is_superuser=True"):
        CustomUser.objects.create_superuser(
            email="admin@example.com",
            password="password",
            is_superuser=False,
        )
