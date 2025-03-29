"""Tests for the custom user model and its manager class."""

from typing import Any

import pytest

from users.models import CustomUser


@pytest.fixture()
def user_data() -> dict[str, str]:
    """Return test data for creating a regular user."""
    return {
        "email": "test@example.com",
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
