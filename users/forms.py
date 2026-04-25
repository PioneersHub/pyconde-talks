"""
Custom forms for user management.

These forms are used for creating and updating users in the admin interface.
Regular users do not have password fields, as they authenticate via email codes only.
Admins can login via email and password, so they have password fields.
"""

from typing import TYPE_CHECKING, Any, ClassVar, cast

from allauth.account.adapter import get_adapter as get_account_adapter
from allauth.account.models import EmailAddress
from allauth.socialaccount.adapter import get_adapter
from allauth.socialaccount.forms import DisconnectForm
from allauth.socialaccount.models import SocialAccount
from django import forms
from django.contrib.auth.forms import ReadOnlyPasswordHashField
from django.utils.translation import gettext_lazy as _

from .models import CustomUser


if TYPE_CHECKING:
    from django_stubs_ext import StrOrPromise

    from .adapters import AccountAdapter


class SuperUserCreationForm(forms.ModelForm[CustomUser]):
    """A form for creating new superusers with required password fields."""

    password1 = forms.CharField(
        label=_("Password"),
        widget=forms.PasswordInput,
        required=True,
        help_text=_("Required for superuser accounts."),
    )

    password2 = forms.CharField(
        label=_("Password confirmation"),
        widget=forms.PasswordInput,
        required=True,
        help_text=_("Enter the same password as above, for verification."),
    )

    class Meta:
        """Meta class for SuperUserCreationForm."""

        model = CustomUser
        fields = ("email", "first_name", "last_name", "is_active", "is_staff")

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Initialize form and set initial values for superuser fields."""
        super().__init__(*args, **kwargs)
        # In the first step of user creation, this field might not be present yet
        if "is_superuser" in self.fields:  # pragma: no cover - only via admin fieldsets
            self.fields["is_superuser"].initial = True
            self.fields["is_superuser"].widget.attrs["disabled"] = True
            self.fields["is_superuser"].help_text = _(
                "Superusers have full access to the admin site.",
            )

    def clean(self) -> dict[str, Any]:
        """Validate form input, ensuring passwords match."""
        cleaned_data = super().clean() or {}
        password1 = cleaned_data.get("password1", "")
        password2 = cleaned_data.get("password2", "")

        if password1 != password2:
            self.add_error("password2", _("The two password fields didn't match."))

        return cleaned_data

    def save(self, commit: bool = True) -> CustomUser:  # noqa: FBT001, FBT002
        """Save the superuser with password."""
        user = super().save(commit=False)

        # Always set superuser flag to True
        user.is_superuser = True

        # Set the password
        user.set_password(self.cleaned_data["password1"])

        if commit:
            user.save()
        return user


class RegularUserCreationForm(forms.ModelForm[CustomUser]):
    """A form for creating new regular users with no password fields."""

    class Meta:
        """Meta class for RegularUserCreationForm."""

        model = CustomUser
        fields = ("email", "first_name", "last_name", "is_active", "is_staff")
        help_texts: ClassVar[dict[str, StrOrPromise]] = {
            "is_staff": _(
                "Staff users can access the admin site but cannot login without being superusers.",
            ),
        }

    def save(self, commit: bool = True) -> CustomUser:  # noqa: FBT001, FBT002
        """Save the user with an unusable password."""
        user = super().save(commit=False)

        # Ensure user is not a superuser
        user.is_superuser = False

        # Always set an unusable password for regular users
        user.set_unusable_password()

        if commit:
            user.save()
        return user


class CustomUserChangeForm(forms.ModelForm[CustomUser]):
    """
    A form for updating users.

    Includes all fields, but replaces the password field with a password hash display field for
    superusers only.
    """

    password = ReadOnlyPasswordHashField(
        label=_("Password"),
        help_text=_(
            "Raw passwords are not stored, so there is no way to see this "
            "user's password, but you can change the password using "
            '<a href="../password/">this form</a>.',
        ),
        required=False,
    )

    class Meta:
        """Meta class for CustomUserChangeForm."""

        model = CustomUser
        fields = (
            "email",
            "password",
            "first_name",
            "last_name",
            "is_active",
            "is_staff",
            "is_superuser",
        )

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Initialize form and remove password field for non-superusers."""
        super().__init__(*args, **kwargs)

        # If this is not a superuser, remove the password field entirely
        if self.instance and not self.instance.is_superuser and "password" in self.fields:
            del self.fields["password"]

    def clean_password(self) -> str:
        """Return the initial value regardless of user input."""
        # Password field is not meant to be changed here
        return str(self.initial.get("password", ""))


class ProfileForm(forms.ModelForm[CustomUser]):
    """Form for users to edit their profile (name and display name)."""

    class Meta:
        """Metadata for ProfileForm."""

        model = CustomUser
        fields = ("first_name", "last_name", "display_name")
        help_texts: ClassVar[dict[str, StrOrPromise]] = {
            "display_name": _(
                "Name shown publicly when asking questions. "
                "If empty, we'll use your full name or email (masked).",
            ),
        }


class DeleteAccountForm(forms.Form):
    """Confirmation form for account deletion."""

    confirm = forms.BooleanField(
        required=True,
        label=_("I understand this action is permanent and cannot be undone"),
    )


class PasswordlessDisconnectForm(DisconnectForm):  # type: ignore[misc]
    """
    Custom disconnect form for passwordless apps.

    allauth's default DisconnectForm blocks disconnect when the user has no usable password. In this
    app all regular users are passwordless, so the default logic would always block disconnect.

    This form replaces that check with two requirements:
    1. The user must have a verified email address (so they have a fallback
       login method after removing Discord).
    2. That email must be recognized by the validation API (i.e. associated
       with a ticket purchase). Without this check, a Discord-only staff user
       could disconnect, change their Discord email, and reconnect to create
       unlimited accounts.
    """

    def clean(self) -> dict[str, Any]:
        """Block disconnect unless the user has an API-authorized email."""
        cleaned_data = forms.Form.clean(self) or {}
        account = cleaned_data.get("account")
        if account:
            self._validate_disconnect_safe(account)
        return cleaned_data

    @staticmethod
    def _validate_disconnect_safe(account: SocialAccount) -> None:
        """Raise a validation error if removing *account* would lock the user out."""
        has_other_accounts = (
            SocialAccount.objects.filter(user_id=account.user_id).exclude(pk=account.pk).exists()
        )
        if has_other_accounts:
            return

        # Last social account - verify the user can still log in without it.
        social_adapter = get_adapter()  # type: ignore[no-untyped-call]
        verified_email = (
            EmailAddress.objects.filter(user=account.user, verified=True)
            .order_by("-primary")
            .values_list("email", flat=True)
            .first()
        )
        if not verified_email:
            error_key = "no_password"
            raise social_adapter.validation_error(error_key)

        # The email exists but must also pass the validation API.
        adapter = cast("AccountAdapter", get_account_adapter())
        if not adapter.can_login_by_email(verified_email):
            error_key = "email_not_authorized"
            raise social_adapter.validation_error(error_key)
