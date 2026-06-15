"""
User management module for authentication and authorization.

This module provides:
- CustomUserManager: Manager class for user operations
- CustomUser: User model with email-based authentication
- InvalidEmailError: Exception for email validation errors
"""

from typing import TYPE_CHECKING, Any, ClassVar, TypedDict, Unpack

from allauth.account.models import EmailAddress
from django.contrib.auth.models import AbstractUser, BaseUserManager
from django.core.exceptions import ValidationError
from django.db import models
from django.utils.translation import gettext_lazy as _

from users.validators import validate_display_name
from utils.email_utils import obfuscate_email


if TYPE_CHECKING:
    from django_stubs_ext.db.models.manager import RelatedManager

    from events.models import Event


class InvalidEmailError(Exception):
    """Exception raised when an invalid email is provided."""

    def __init__(self, email: str) -> None:
        """
        Initialize the InvalidEmailError.

        Args:
            email: The invalid email that caused the error

        """
        self.email = email
        super().__init__(f"Invalid email address: {obfuscate_email(email)}")


class CreateUserExtraFields(TypedDict, total=False):
    """Extra fields for create_user (excludes email/password)."""

    is_active: bool
    is_staff: bool
    is_superuser: bool


class CustomUserManager(BaseUserManager["CustomUser"]):
    """Manage user operations with email-based authentication."""

    def create_user(
        self,
        email: str,
        password: str | None = None,
        **extra_fields: Unpack[CreateUserExtraFields],
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
                if not password:  # pragma: no cover
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
        **extra_fields: Unpack[CreateUserExtraFields],
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
        except ValidationError as exc:  # pragma: no cover
            msg = "Error creating superuser"
            raise ValidationError(msg) from exc


class CustomUser(AbstractUser):
    """Custom user model using email-based authentication instead of username."""

    username = None  # type: ignore[assignment]
    email = models.EmailField(
        _("email address"),
        unique=True,
        error_messages={
            "unique": _("A user with that email already exists."),
        },
    )
    display_name = models.CharField(
        max_length=100,
        blank=True,
        validators=[validate_display_name],
        help_text=_(
            "Public name shown when asking questions (optional). "
            "If blank, we'll use your full name or email.",
        ),
    )

    events: models.ManyToManyField[Event, Event] = models.ManyToManyField(
        "events.Event",
        related_name="users",
        blank=True,
        help_text=_("Events the user has access to"),
    )

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS: ClassVar[list[str]] = []

    objects: ClassVar[CustomUserManager] = CustomUserManager()  # type: ignore[assignment]

    emailaddress_set: RelatedManager[EmailAddress]
    tickets: RelatedManager[Ticket]

    class Meta:
        """Metadata for CustomUser model."""

        verbose_name = _("user")
        verbose_name_plural = _("users")

    def __str__(self) -> str:
        """Return string representation of the user."""
        return str(self.email)

    def clean(self) -> None:
        """
        Validate the user model.

        Ensures email is lowercase before saving.
        """
        super().clean()
        self.email = self.email.lower()

    def label(self, *, obfuscate: bool = False) -> str:
        """
        Return the most human-readable name for this user.

        Prefers the chosen display name, then the full name, then the email. Pass
        ``obfuscate=True`` to mask the email fallback for public contexts (e.g. the Q&A author
        line); moderator-facing callers show it verbatim. Returns an empty string only when all
        three are empty, so callers that want an "Anonymous" fallback should coalesce.
        """
        email = obfuscate_email(self.email) if obfuscate else self.email
        return self.display_name.strip() or self.get_full_name().strip() or email

    def visible_events(self) -> models.QuerySet[Event]:
        """
        Return active events visible to this user, ordered by name.

        Superusers see all active events; regular users see only their linked events.
        """
        # Resolve the Event model from the M2M descriptor so we don't need a
        # runtime import of events.models (which would be circular).
        event_model = self.events.model
        base = event_model.objects.all() if self.is_superuser else self.events.all()
        return base.filter(is_active=True).order_by("name")

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Save superuser with a password and non-superusers without a password."""
        if self.is_superuser and not self.password:
            msg = "Superusers must have a password"
            raise ValidationError(msg)
        if not self.is_superuser and self.has_usable_password():
            self.set_unusable_password()
        super().save(*args, **kwargs)


MAX_TICKET_ID_LENGTH = 10


class Ticket(models.Model):
    """Links a user to an event via a unique ticket identifier."""

    user = models.ForeignKey(
        "users.CustomUser",
        on_delete=models.CASCADE,
        related_name="tickets",
    )
    event = models.ForeignKey(
        "events.Event",
        on_delete=models.CASCADE,
        related_name="tickets",
    )
    ticket_id = models.CharField(
        max_length=MAX_TICKET_ID_LENGTH,
        help_text=_("Unique ticket identifier for this event."),
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        """Metadata for the Ticket model."""

        verbose_name = _("Ticket")
        verbose_name_plural = _("Tickets")
        constraints: ClassVar[list[models.UniqueConstraint]] = [
            models.UniqueConstraint(
                fields=["event", "ticket_id"],
                name="unique_ticket_per_event",
            ),
        ]

    def __str__(self) -> str:
        """Return the ticket ID and event."""
        return f"{self.ticket_id} ({self.event})"
