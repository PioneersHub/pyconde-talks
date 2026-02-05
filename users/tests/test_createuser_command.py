"""Tests for the createuser management command."""

import pytest
from django.core.management import call_command

from users.models import CustomUser, InvalidEmailError


@pytest.mark.django_db
class TestCreateUserCommand:
    """Tests for the createuser management command."""

    def test_create_user_success(self) -> None:
        """Create an active user with an unusable password from the given email."""
        call_command("createuser", email="cmd@example.com")
        assert CustomUser.objects.filter(email="cmd@example.com").exists()
        user = CustomUser.objects.get(email="cmd@example.com")
        assert user.is_active is True
        assert not user.has_usable_password()

    def test_create_user_invalid_email(self) -> None:
        """Raise InvalidEmailError when an empty email is provided."""
        with pytest.raises(InvalidEmailError):
            call_command("createuser", email="")

    def test_create_user_duplicate_email(self) -> None:
        """Raise InvalidEmailError when the email is already in use."""
        call_command("createuser", email="dup@example.com")
        with pytest.raises(InvalidEmailError):
            call_command("createuser", email="dup@example.com")
