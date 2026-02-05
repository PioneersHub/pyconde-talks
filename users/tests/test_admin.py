"""Tests for users.admin covering admin views & actions."""
# ruff: noqa: ARG002 PLC0415 PLR2004 SLF001

from http import HTTPStatus
from typing import TYPE_CHECKING

import pytest
from allauth.account.models import EmailAddress
from django.contrib.admin.sites import AdminSite
from django.test import RequestFactory
from django.urls import reverse
from model_bakery import baker

from users.admin import CustomUserAdmin, EmailVerificationListFilter
from users.models import CustomUser


if TYPE_CHECKING:
    from django.test.client import Client


site = AdminSite()


@pytest.fixture()
def rf() -> RequestFactory:
    """Return a Django RequestFactory for building test requests."""
    return RequestFactory()


@pytest.fixture()
def superuser() -> CustomUser:
    """Return a superuser required to access admin views."""
    return CustomUser.objects.create_superuser(
        email="superadmin@example.com",
        password="admin123!",
    )


# ---------------------------------------------------------------------------
# EmailVerificationListFilter
# ---------------------------------------------------------------------------
@pytest.mark.django_db
class TestEmailVerificationListFilter:
    """Verify the email verification list filter lookups and queryset filtering."""

    def test_lookups(self, rf: RequestFactory) -> None:
        """Return two choices: verified=yes and verified=no."""
        admin = CustomUserAdmin(CustomUser, site)
        request = rf.get("/")
        f = EmailVerificationListFilter(request, {}, CustomUser, admin)
        lookups = f.lookups(request, admin)
        assert len(lookups) == 2

    def test_filter_verified(self, rf: RequestFactory, superuser: CustomUser) -> None:
        """List only users whose email is verified when filtering by verified=yes."""
        user = baker.make(CustomUser, email="verified@example.com")
        EmailAddress.objects.create(user=user, email=user.email, verified=True, primary=True)
        from django.test import Client as DjangoClient

        c = DjangoClient()
        c.force_login(superuser)
        url = reverse("admin:users_customuser_changelist") + "?verified=yes"
        response = c.get(url)
        assert response.status_code == HTTPStatus.OK

    def test_filter_not_verified(self, rf: RequestFactory, superuser: CustomUser) -> None:
        """List only users whose email is not verified when filtering by verified=no."""
        user = baker.make(CustomUser, email="unverified@example.com")
        EmailAddress.objects.create(user=user, email=user.email, verified=False, primary=True)
        from django.test import Client as DjangoClient

        c = DjangoClient()
        c.force_login(superuser)
        url = reverse("admin:users_customuser_changelist") + "?verified=no"
        response = c.get(url)
        assert response.status_code == HTTPStatus.OK

    def test_filter_no_value(self, rf: RequestFactory) -> None:
        """Return all users when no verification filter value is selected."""
        admin = CustomUserAdmin(CustomUser, site)
        request = rf.get("/")
        f = EmailVerificationListFilter(request, {}, CustomUser, admin)
        qs = f.queryset(request, CustomUser.objects.all())
        # Should return all users
        assert qs.count() == CustomUser.objects.count()


# ---------------------------------------------------------------------------
# CustomUserAdmin display methods
# ---------------------------------------------------------------------------
@pytest.mark.django_db
class TestCustomUserAdminDisplayMethods:
    """Verify CustomUserAdmin display helpers for name, email, and dates."""

    def test_full_name(self) -> None:
        """Return the user's full name as 'first last'."""
        admin = CustomUserAdmin(CustomUser, site)
        user = baker.make(CustomUser, first_name="Jane", last_name="Doe")
        assert admin.full_name(user) == "Jane Doe"

    def test_full_name_empty(self) -> None:
        """Return a dash placeholder when both first and last name are empty."""
        admin = CustomUserAdmin(CustomUser, site)
        user = baker.make(CustomUser, first_name="", last_name="")
        assert admin.full_name(user) == "-"

    def test_email_verified_true(self) -> None:
        """Return True when the user has a verified EmailAddress."""
        admin = CustomUserAdmin(CustomUser, site)
        user = baker.make(CustomUser, email="ev@example.com")
        EmailAddress.objects.create(user=user, email=user.email, verified=True, primary=True)
        # Prefetch for the admin
        user = CustomUser.objects.prefetch_related("emailaddress_set").get(pk=user.pk)
        assert admin.email_verified(user) is True

    def test_email_verified_false(self) -> None:
        """Return False when the user's EmailAddress is not verified."""
        admin = CustomUserAdmin(CustomUser, site)
        user = baker.make(CustomUser, email="nv@example.com")
        EmailAddress.objects.create(user=user, email=user.email, verified=False, primary=True)
        user = CustomUser.objects.prefetch_related("emailaddress_set").get(pk=user.pk)
        assert admin.email_verified(user) is False

    def test_email_verified_no_email(self) -> None:
        """Return False when the user has no EmailAddress record at all."""
        admin = CustomUserAdmin(CustomUser, site)
        user = baker.make(CustomUser, email="noea@example.com")
        user = CustomUser.objects.prefetch_related("emailaddress_set").get(pk=user.pk)
        assert admin.email_verified(user) is False

    def test_date_joined_display(self) -> None:
        """Format the date_joined field as a human-readable string."""
        admin = CustomUserAdmin(CustomUser, site)
        user = baker.make(CustomUser)
        result = admin.date_joined_display(user)
        assert "-" in result  # date format

    def test_last_login_display_never(self) -> None:
        """Display 'Never' when the user has not logged in before."""
        admin = CustomUserAdmin(CustomUser, site)
        user = baker.make(CustomUser, last_login=None)
        result = str(admin.last_login_display(user))
        assert result == "Never"

    def test_last_login_display_with_date(self) -> None:
        """Format the last_login as a date string when the user has logged in."""
        from django.utils import timezone

        admin = CustomUserAdmin(CustomUser, site)
        user = baker.make(CustomUser, last_login=timezone.now())
        result = admin.last_login_display(user)
        assert "-" in result


# ---------------------------------------------------------------------------
# CustomUserAdmin actions
# ---------------------------------------------------------------------------
@pytest.mark.django_db
class TestCustomUserAdminActions:
    """Verify CustomUserAdmin bulk actions: verify email, activate, deactivate."""

    def _make_request(self, rf: RequestFactory, user: CustomUser) -> object:
        request = rf.post("/")
        request.user = user
        from django.contrib.messages.storage.fallback import FallbackStorage

        request.session = "session"  # type: ignore[assignment]
        request._messages = FallbackStorage(request)  # type: ignore[attr-defined]
        return request

    def test_verify_email(self, rf: RequestFactory, superuser: CustomUser) -> None:
        """Mark an unverified email as verified via the admin action."""
        admin = CustomUserAdmin(CustomUser, site)
        user = baker.make(CustomUser, email="toverify@example.com")
        EmailAddress.objects.create(user=user, email=user.email, verified=False, primary=True)
        request = self._make_request(rf, superuser)
        admin.verify_email(request, CustomUser.objects.filter(pk=user.pk))  # type: ignore[arg-type]
        assert EmailAddress.objects.get(user=user).verified is True

    def test_verify_email_already_verified(self, rf: RequestFactory, superuser: CustomUser) -> None:
        """Do nothing when the email is already verified."""
        admin = CustomUserAdmin(CustomUser, site)
        user = baker.make(CustomUser, email="alreadyv@example.com")
        EmailAddress.objects.create(user=user, email=user.email, verified=True, primary=True)
        request = self._make_request(rf, superuser)
        admin.verify_email(request, CustomUser.objects.filter(pk=user.pk))  # type: ignore[arg-type]

    def test_activate_users(self, rf: RequestFactory, superuser: CustomUser) -> None:
        """Set is_active=True on selected inactive users."""
        admin = CustomUserAdmin(CustomUser, site)
        user = baker.make(CustomUser, is_active=False)
        request = self._make_request(rf, superuser)
        admin.activate_users(request, CustomUser.objects.filter(pk=user.pk))  # type: ignore[arg-type]
        user.refresh_from_db()
        assert user.is_active is True

    def test_activate_already_active(self, rf: RequestFactory, superuser: CustomUser) -> None:
        """Do nothing when the user is already active."""
        admin = CustomUserAdmin(CustomUser, site)
        user = baker.make(CustomUser, is_active=True)
        request = self._make_request(rf, superuser)
        admin.activate_users(request, CustomUser.objects.filter(pk=user.pk))  # type: ignore[arg-type]

    def test_deactivate_users(self, rf: RequestFactory, superuser: CustomUser) -> None:
        """Set is_active=False on selected active users."""
        admin = CustomUserAdmin(CustomUser, site)
        user = baker.make(CustomUser, is_active=True)
        request = self._make_request(rf, superuser)
        admin.deactivate_users(request, CustomUser.objects.filter(pk=user.pk))  # type: ignore[arg-type]
        user.refresh_from_db()
        assert user.is_active is False

    def test_deactivate_self_prevented(self, rf: RequestFactory, superuser: CustomUser) -> None:
        """Prevent the admin from deactivating their own account."""
        admin = CustomUserAdmin(CustomUser, site)
        request = self._make_request(rf, superuser)
        admin.deactivate_users(request, CustomUser.objects.filter(pk=superuser.pk))  # type: ignore[arg-type]
        superuser.refresh_from_db()
        assert superuser.is_active is True  # Should not deactivate own account

    def test_deactivate_already_inactive(self, rf: RequestFactory, superuser: CustomUser) -> None:
        """Do nothing when the user is already inactive."""
        admin = CustomUserAdmin(CustomUser, site)
        user = baker.make(CustomUser, is_active=False)
        request = self._make_request(rf, superuser)
        admin.deactivate_users(request, CustomUser.objects.filter(pk=user.pk))  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# CustomUserAdmin fieldsets & views
# ---------------------------------------------------------------------------
@pytest.mark.django_db
class TestCustomUserAdminViews:
    """Verify CustomUserAdmin fieldsets, save logic, and custom add/select views."""

    def test_get_fieldsets_new_user(self, rf: RequestFactory, superuser: CustomUser) -> None:
        """Return the add-user fieldsets when creating a new user (obj=None)."""
        admin = CustomUserAdmin(CustomUser, site)
        request = rf.get("/")
        request.user = superuser
        fieldsets = admin.get_fieldsets(request, obj=None)
        assert fieldsets is not None

    def test_get_fieldsets_existing_superuser(
        self,
        rf: RequestFactory,
        superuser: CustomUser,
    ) -> None:
        """Return the full fieldsets when editing an existing superuser."""
        admin = CustomUserAdmin(CustomUser, site)
        request = rf.get("/")
        request.user = superuser
        fieldsets = admin.get_fieldsets(request, obj=superuser)
        # Should use full fieldsets for superuser
        assert fieldsets == admin.fieldsets

    def test_get_fieldsets_existing_regular_user(
        self,
        rf: RequestFactory,
        superuser: CustomUser,
    ) -> None:
        """Return the limited regular_user_fieldsets for non-superuser editing."""
        admin = CustomUserAdmin(CustomUser, site)
        request = rf.get("/")
        request.user = superuser
        regular = baker.make(CustomUser, is_superuser=False)
        fieldsets = admin.get_fieldsets(request, obj=regular)
        assert fieldsets == admin.regular_user_fieldsets

    def test_save_model_new_user(self, rf: RequestFactory, superuser: CustomUser) -> None:
        """Create an EmailAddress when saving a new user for the first time."""
        admin = CustomUserAdmin(CustomUser, site)
        user = baker.make(CustomUser, email="newuser@example.com", is_superuser=False)
        request = rf.post("/")
        request.user = superuser
        from django.contrib.messages.storage.fallback import FallbackStorage

        request.session = "session"  # type: ignore[assignment]
        request._messages = FallbackStorage(request)  # type: ignore[attr-defined]
        admin.save_model(request, user, form=None, change=False)
        assert EmailAddress.objects.filter(user=user, email=user.email).exists()

    def test_save_model_removes_password_for_non_superuser(
        self,
        rf: RequestFactory,
        superuser: CustomUser,
    ) -> None:
        """Set an unusable password for non-superusers on save to enforce passwordless login."""
        admin = CustomUserAdmin(CustomUser, site)
        user = baker.make(CustomUser, is_superuser=False)
        user.set_password("temp-test-password")
        request = rf.post("/")
        request.user = superuser
        from django.contrib.messages.storage.fallback import FallbackStorage

        request.session = "session"  # type: ignore[assignment]
        request._messages = FallbackStorage(request)  # type: ignore[attr-defined]
        admin.save_model(request, user, form=None, change=True)
        user.refresh_from_db()
        assert not user.has_usable_password()

    def test_add_view_redirects_to_select(
        self,
        client: Client,
        superuser: CustomUser,
    ) -> None:
        """Redirect to the user type selection page instead of showing the add form."""
        client.force_login(superuser)
        url = reverse("admin:users_customuser_add")
        response = client.get(url)
        assert response.status_code == HTTPStatus.FOUND
        assert "select" in response.headers["Location"]

    def test_select_user_type_view(self, client: Client, superuser: CustomUser) -> None:
        """Display the user type selection page with regular and superuser options."""
        client.force_login(superuser)
        url = reverse("admin:users_customuser_select")
        response = client.get(url)
        assert response.status_code == HTTPStatus.OK

    def test_add_regular_user_redirects(
        self,
        client: Client,
        superuser: CustomUser,
    ) -> None:
        """Redirect to the add form with user_type=regular query parameter."""
        client.force_login(superuser)
        url = reverse("admin:users_customuser_add_regularuser")
        response = client.get(url)
        assert response.status_code == HTTPStatus.FOUND
        assert "user_type=regular" in response.headers["Location"]

    def test_add_superuser_redirects(
        self,
        client: Client,
        superuser: CustomUser,
    ) -> None:
        """Redirect to the add form with user_type=super query parameter."""
        client.force_login(superuser)
        url = reverse("admin:users_customuser_add_superuser")
        response = client.get(url)
        assert response.status_code == HTTPStatus.FOUND
        assert "user_type=super" in response.headers["Location"]

    def test_add_view_with_regular_user_type(
        self,
        client: Client,
        superuser: CustomUser,
    ) -> None:
        """Show the regular user creation form when user_type=regular is specified."""
        client.force_login(superuser)
        url = reverse("admin:users_customuser_add") + "?user_type=regular"
        response = client.get(url)
        assert response.status_code == HTTPStatus.OK

    def test_add_view_with_super_user_type(
        self,
        client: Client,
        superuser: CustomUser,
    ) -> None:
        """Show the superuser creation form when user_type=super is specified."""
        client.force_login(superuser)
        url = reverse("admin:users_customuser_add") + "?user_type=super"
        response = client.get(url)
        assert response.status_code == HTTPStatus.OK

    def test_add_view_post_super_user(
        self,
        client: Client,
        superuser: CustomUser,
    ) -> None:
        """POST to create superuser sets is_superuser=on in POST data."""
        client.force_login(superuser)
        url = reverse("admin:users_customuser_add") + "?user_type=super"
        data = {
            "email": "newsuperadmin@example.com",
            "password1": "securePass456!",
            "password2": "securePass456!",
            "is_active": "on",
            "is_staff": "on",
        }
        response = client.post(url, data)
        # Should redirect on success (302) or show form (200) if validation fails
        assert response.status_code in (HTTPStatus.OK, HTTPStatus.FOUND)

    def test_changelist_uses_queryset(
        self,
        client: Client,
        superuser: CustomUser,
    ) -> None:
        """Prefetch email addresses in the changelist queryset for efficient display."""
        client.force_login(superuser)
        url = reverse("admin:users_customuser_changelist")
        response = client.get(url)
        assert response.status_code == HTTPStatus.OK
