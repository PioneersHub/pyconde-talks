"""
User management module for authentication and authorization.

This module provides:
- CustomUserManager: Manager class for user operations
- CustomUser: User model with email-based authentication
- InvalidEmailError: Exception for email validation errors
"""

from typing import Any, ClassVar

from allauth.account.models import EmailAddress
from django.contrib.auth.models import AbstractUser, BaseUserManager
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _


class InvalidEmailError(Exception):
    """Exception raised when an invalid email is provided."""

    def __init__(self, email: str) -> None:
        """
        Initialize the InvalidEmailError.

        Args:
            email: The invalid email that caused the error

        """
        self.email = email
        super().__init__(f"Invalid email address: {email}")


class CustomUserManager(BaseUserManager):
    """Manage user operations with email-based authentication."""

    def create_user(
        self,
        email: str,
        password: str | None = None,
        **extra_fields: dict[str, Any],
    ) -> CustomUser:
        """
        Create and save a new user with a verified email address.

        Args:
            email: The email address for the new user
            password: Optional password for the new user
            **extra_fields: Additional fields to be saved on the user model

        Returns:
            CustomUser: The newly created user instance

        Raises:
            InvalidEmailError: If the email is invalid or not provided

        """
        if not email:
            raise InvalidEmailError(email) from None

        try:
            email = self.normalize_email(email).lower()
            user = self.model(email=email, **extra_fields)

            if extra_fields.get("is_superuser"):
                if not password:
                    msg = "Superuser must have a password"
                    raise ValueError(msg)
                user.set_password(password)
            else:
                user.set_unusable_password()

            user.full_clean()
        except ValidationError as exc:
            raise InvalidEmailError(email) from exc
        else:
            user.save(using=self._db)
            EmailAddress.objects.create(
                user=user,
                email=email,
                primary=True,
                verified=True,
            )
            return user

    def create_superuser(
        self,
        email: str,
        password: str,
        **extra_fields: dict[str, Any],
    ) -> CustomUser:
        """
        Create and save a new superuser with a verified email address.

        Args:
            email: The email address for the new superuser
            password: Password for the new superuser
            **extra_fields: Additional fields to be saved on the user model

        Returns:
            CustomUser: The newly created superuser instance

        Raises:
            InvalidEmailError: If the email is invalid or not provided
            ValidationError: If superuser flags are not properly set

        """
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        extra_fields.setdefault("is_active", True)

        if not extra_fields.get("is_staff"):
            msg = "Superuser must have is_staff=True"
            raise ValidationError(msg)

        if not extra_fields.get("is_superuser"):
            msg = "Superuser must have is_superuser=True"
            raise ValidationError(msg)

        if not password:
            msg = "Superuser must have a password"
            raise ValueError(msg)

        try:
            return self.create_user(email, password, **extra_fields)
        except ValidationError as exc:
            msg = "Error creating superuser"
            raise ValidationError(msg) from exc


class Meta:
    """Metadata for CustomUser model."""

    verbose_name = "user"
    verbose_name_plural = "users"


class CustomUser(AbstractUser):
    """Custom user model using email-based authentication instead of username."""

    username = None
    email = models.EmailField(
        "email address",
        unique=True,
        error_messages={
            "unique": "A user with that email already exists.",
        },
    )
    display_name = models.CharField(
        max_length=100,
        blank=True,
        help_text=_(
            "Public name shown when asking questions (optional). "
            "If blank, we'll use your full name or email.",
        ),
    )
    date_joined = models.DateTimeField(default=timezone.now)

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS: ClassVar[list] = []

    objects = CustomUserManager()

    Meta = Meta

    def __str__(self) -> str:
        """Return string representation of the user."""
        return self.email

    def clean(self) -> None:
        """
        Validate the user model.

        Ensures email is lowercase before saving.
        """
        super().clean()
        self.email = self.email.lower()

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Save superuser with a password and non-superusers without a password."""
        if self.is_superuser and not self.password:
            msg = "Superusers must have a password"
            raise ValidationError(msg)
        if not self.is_superuser and self.has_usable_password():
            self.set_unusable_password()
        super().save(*args, **kwargs)
