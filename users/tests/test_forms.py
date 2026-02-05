"""Tests for users.forms covering all form classes."""

import pytest
from model_bakery import baker

from users.forms import (
    CustomUserChangeForm,
    ProfileForm,
    RegularUserCreationForm,
    SuperUserCreationForm,
)
from users.models import CustomUser


@pytest.mark.django_db
class TestSuperUserCreationForm:
    """Verify SuperUserCreationForm validates credentials and sets superuser flags."""

    def test_valid_form(self) -> None:
        """Accept valid email, matching passwords, and required boolean fields."""
        data = {
            "email": "admin@example.com",
            "password1": "securePass123!",
            "password2": "securePass123!",
            "is_active": True,
            "is_staff": True,
        }
        form = SuperUserCreationForm(data=data)
        assert form.is_valid(), form.errors

    def test_password_mismatch(self) -> None:
        """Reject the form when password1 and password2 do not match."""
        data = {
            "email": "admin@example.com",
            "password1": "securePass123!",
            "password2": "differentPass!",
            "is_active": True,
            "is_staff": True,
        }
        form = SuperUserCreationForm(data=data)
        assert not form.is_valid()
        assert "password2" in form.errors

    def test_save_sets_superuser(self) -> None:
        """Set is_superuser=True and save a usable password on form save."""
        data = {
            "email": "super@example.com",
            "password1": "securePass123!",
            "password2": "securePass123!",
            "is_active": True,
            "is_staff": True,
        }
        form = SuperUserCreationForm(data=data)
        assert form.is_valid()
        user = form.save()
        assert user.is_superuser is True
        assert user.has_usable_password()

    def test_save_no_commit(self) -> None:
        """Set is_superuser=True without persisting the user to the database."""
        data = {
            "email": "nocommit@example.com",
            "password1": "securePass123!",
            "password2": "securePass123!",
            "is_active": True,
            "is_staff": True,
        }
        form = SuperUserCreationForm(data=data)
        assert form.is_valid()
        user = form.save(commit=False)
        assert user.is_superuser is True
        assert user.pk is None


@pytest.mark.django_db
class TestRegularUserCreationForm:
    """Verify RegularUserCreationForm creates users with unusable passwords."""

    def test_valid_form(self) -> None:
        """Accept email and boolean fields without requiring a password."""
        data = {
            "email": "regular@example.com",
            "is_active": True,
            "is_staff": False,
        }
        form = RegularUserCreationForm(data=data)
        assert form.is_valid(), form.errors

    def test_save_sets_unusable_password(self) -> None:
        """Save the user with an unusable password and is_superuser=False."""
        data = {
            "email": "regular2@example.com",
            "is_active": True,
            "is_staff": False,
        }
        form = RegularUserCreationForm(data=data)
        assert form.is_valid()
        user = form.save()
        assert user.is_superuser is False
        assert not user.has_usable_password()

    def test_save_no_commit(self) -> None:
        """Set an unusable password without persisting to the database."""
        data = {
            "email": "nocommit2@example.com",
            "is_active": True,
            "is_staff": False,
        }
        form = RegularUserCreationForm(data=data)
        assert form.is_valid()
        user = form.save(commit=False)
        assert user.pk is None
        assert not user.has_usable_password()


@pytest.mark.django_db
class TestCustomUserChangeForm:
    """Verify CustomUserChangeForm adjusts fields based on superuser status."""

    def test_superuser_has_password_field(self) -> None:
        """Include the password field when editing a superuser."""
        user = CustomUser.objects.create_superuser(
            email="changesu@example.com",
            password="pass123!",
        )
        form = CustomUserChangeForm(instance=user)
        assert "password" in form.fields

    def test_regular_user_no_password_field(self) -> None:
        """Exclude the password field when editing a regular (non-superuser) user."""
        user = CustomUser.objects.create_user(email="changeuser@example.com")
        form = CustomUserChangeForm(instance=user)
        assert "password" not in form.fields

    def test_clean_password_returns_initial(self) -> None:
        """Return the initial password value to prevent accidental password changes."""
        user = CustomUser.objects.create_superuser(
            email="cleanpw@example.com",
            password="pass123!",
        )
        form = CustomUserChangeForm(instance=user)
        result = form.clean_password()
        assert result == str(form.initial.get("password", ""))


@pytest.mark.django_db
class TestProfileForm:
    """Verify ProfileForm validates and saves display name and name fields."""

    def test_valid_form(self) -> None:
        """Accept first_name, last_name, and display_name fields."""
        user = baker.make(CustomUser)
        data = {
            "first_name": "Jane",
            "last_name": "Doe",
            "display_name": "JaneDoe",
        }
        form = ProfileForm(data=data, instance=user)
        assert form.is_valid()

    def test_save(self) -> None:
        """Persist the updated display_name to the database."""
        user = baker.make(CustomUser)
        data = {
            "first_name": "Updated",
            "last_name": "Name",
            "display_name": "Updated Name",
        }
        form = ProfileForm(data=data, instance=user)
        assert form.is_valid()
        saved_user = form.save()
        assert saved_user.display_name == "Updated Name"
