"""Admin interface for users."""

from typing import TYPE_CHECKING, Any, ClassVar

from allauth.account.models import EmailAddress
from django.contrib import admin, messages
from django.contrib.auth.admin import UserAdmin
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect
from django.shortcuts import render
from django.urls import URLPattern, path, reverse
from django.utils.translation import gettext_lazy as _

from .forms import CustomUserChangeForm, RegularUserCreationForm, SuperUserCreationForm
from .models import CustomUser


if TYPE_CHECKING:
    from django.db.models.query import QuerySet
    from django_stubs_ext import StrOrPromise


class EmailVerificationListFilter(admin.SimpleListFilter):
    """Filter users by email verification status."""

    title = _("Email verification")
    parameter_name = "verified"

    def lookups(self, _request: HttpRequest, _model_admin: Any) -> list[tuple[str, StrOrPromise]]:
        """Return filter options."""
        return [
            ("yes", _("Verified")),
            ("no", _("Not verified")),
        ]

    def queryset(
        self,
        _request: HttpRequest,
        queryset: QuerySet[CustomUser],
    ) -> QuerySet[CustomUser]:
        """Filter queryset based on selected option."""
        if self.value() == "yes":
            return queryset.filter(emailaddress__verified=True)
        if self.value() == "no":
            return queryset.filter(emailaddress__verified=False)
        return queryset


class EmailAddressInline(admin.TabularInline[EmailAddress, CustomUser]):
    """Inline admin interface for EmailAddress objects."""

    model = EmailAddress
    extra = 0
    readonly_fields: ClassVar[list[str]] = ["email", "verified", "primary"]
    can_delete = False
    verbose_name = _("Email Address")
    verbose_name_plural = _("Email Addresses")
    fields = ("email", "verified", "primary")

    def has_add_permission(self, request: HttpRequest, obj: CustomUser | None = None) -> bool:  # noqa: ARG002
        """Prevent adding email addresses directly through admin."""
        return False


@admin.register(CustomUser)
class CustomUserAdmin(UserAdmin[CustomUser]):
    """Admin configuration for the CustomUser model using UserAdmin."""

    # Form configuration
    form = CustomUserChangeForm
    add_form: type[SuperUserCreationForm | RegularUserCreationForm] = SuperUserCreationForm

    # Override UserAdmin username handling
    username_field = "email"

    # Display configuration
    list_display = (
        "email",
        "full_name",
        "is_active",
        "is_staff",
        "is_superuser",
        "email_verified",
        "last_login_display",
        "date_joined_display",
    )

    list_filter = (
        EmailVerificationListFilter,
        "is_staff",
        "is_superuser",
        "is_active",
        "groups",
        "date_joined",
        "last_login",
    )

    search_fields = ("email", "first_name", "last_name")
    ordering = ("-date_joined",)
    actions = ("verify_email", "activate_users", "deactivate_users")

    # Override UserAdmin fieldsets to use email instead of username
    fieldsets = (
        (None, {"fields": ("email", "password")}),
        (_("Personal info"), {"fields": ("first_name", "last_name")}),
        (
            _("Permissions"),
            {
                "fields": (
                    "is_active",
                    "is_staff",
                    "is_superuser",
                    "groups",
                    "user_permissions",
                ),
                "description": _(
                    "Only superusers and staff members can log in to the admin site. "
                    "Normal users login through the passwordless authentication.",
                ),
            },
        ),
        (
            _("Important dates"),
            {
                "fields": ("last_login", "date_joined"),
                "description": _("Shows when the user first registered and last logged in."),
            },
        ),
    )

    # Field layout for regular users in edit mode (no password)
    regular_user_fieldsets = (
        (None, {"fields": ("email",)}),
        (_("Personal info"), {"fields": ("first_name", "last_name")}),
        (
            _("Permissions"),
            {
                "fields": (
                    "is_active",
                    "is_staff",
                    "is_superuser",
                    "groups",
                    "user_permissions",
                ),
                "description": _(
                    "Only superusers and staff members can log in to the admin site. "
                    "Normal users login through the passwordless authentication.",
                ),
            },
        ),
        (
            _("Important dates"),
            {
                "fields": ("last_login", "date_joined"),
                "description": _("Shows when the user first registered and last logged in."),
            },
        ),
    )

    # Override UserAdmin add_fieldsets to use email instead of username
    add_fieldsets = (
        (
            None,
            {
                "classes": ("wide",),
                "fields": (
                    "email",
                    "password1",
                    "password2",
                    "first_name",
                    "last_name",
                    "is_active",
                    "is_staff",
                    "is_superuser",
                ),
            },
        ),
    )

    # Define add fieldsets for superusers (with password)
    superuser_add_fieldsets = (
        (
            None,
            {
                "classes": ("wide",),
                "fields": (
                    "email",
                    "password1",
                    "password2",
                    "first_name",
                    "last_name",
                    "is_active",
                    "is_staff",
                ),
                "description": _("Creating a superuser account with password authentication."),
            },
        ),
    )

    # Define add fieldsets for regular users (no password)
    regular_user_add_fieldsets = (
        (
            None,
            {
                "classes": ("wide",),
                "fields": ("email", "first_name", "last_name", "is_active", "is_staff"),
                "description": _("Creating a regular user with passwordless authentication."),
            },
        ),
    )

    readonly_fields = ("date_joined", "last_login")
    inlines = (EmailAddressInline,)
    list_per_page = 25

    def get_urls(self) -> list[URLPattern]:
        """Add custom URLs without recursion issues."""
        urls = super().get_urls()

        # Define custom URLs for user type selection and creation
        custom_urls = [
            path(
                "add/select/",
                self.admin_site.admin_view(self.select_user_type),
                name="users_customuser_select",
            ),
            path(
                "add/regularuser/",
                self.admin_site.admin_view(self.add_regular_user),
                name="users_customuser_add_regularuser",
            ),
            path(
                "add/superuser/",
                self.admin_site.admin_view(self.add_superuser),
                name="users_customuser_add_superuser",
            ),
        ]
        return custom_urls + urls

    def add_view(
        self,
        request: HttpRequest,
        form_url: str = "",
        extra_context: dict[str, Any] | None = None,
    ) -> HttpResponse:
        """Override add view to redirect to selection page."""
        # If accessing /add/ directly, redirect to selection page
        if not form_url and "user_type" not in request.GET:
            return HttpResponseRedirect(
                reverse("admin:users_customuser_select"),
            )

        # If user type is specified, set up the appropriate form and fieldsets
        user_type = request.GET.get("user_type")
        if user_type == "regular":
            self.add_form = RegularUserCreationForm
            self.add_fieldsets = self.regular_user_add_fieldsets  # type: ignore[assignment]
        elif user_type == "super":
            self.add_form = SuperUserCreationForm
            self.add_fieldsets = self.superuser_add_fieldsets  # type: ignore[assignment]

        # Handle POST data for superusers
        if request.method == "POST" and user_type == "super":
            post_data = request.POST.copy()
            post_data["is_superuser"] = "on"
            request.POST = post_data  # type: ignore[assignment]

        return super().add_view(request, form_url, extra_context)

    def select_user_type(self, request: HttpRequest) -> HttpResponse:
        """Display user type selection page."""
        context = self.admin_site.each_context(request)
        context["title"] = _("Select user type")
        context["superuser_url"] = f"{reverse('admin:users_customuser_add')}?user_type=super"
        context["regularuser_url"] = f"{reverse('admin:users_customuser_add')}?user_type=regular"

        return render(request, "admin/users/customuser/add_selection.html", context)

    def add_regular_user(self, _: HttpRequest) -> HttpResponseRedirect:
        """Redirect to add view with regular user type."""
        return HttpResponseRedirect(
            f"{reverse('admin:users_customuser_add')}?user_type=regular",
        )

    def add_superuser(self, _: HttpRequest) -> HttpResponseRedirect:
        """Redirect to add view with superuser type."""
        return HttpResponseRedirect(
            f"{reverse('admin:users_customuser_add')}?user_type=super",
        )

    def get_queryset(self, request: HttpRequest) -> QuerySet[CustomUser]:
        """Optimize query by prefetching related email addresses and groups."""
        queryset = super().get_queryset(request)
        return queryset.prefetch_related("emailaddress_set", "groups")

    @admin.display(description=_("Full Name"))
    def full_name(self, obj: CustomUser) -> str:
        """Display the user's full name."""
        return f"{obj.first_name} {obj.last_name}".strip() or "-"

    @admin.display(boolean=True, description=_("Email Verified"))
    def email_verified(self, obj: CustomUser) -> bool:
        """Display email verification status with standard Django boolean icon."""
        try:
            email_address = obj.emailaddress_set.first()
        except (AttributeError, IndexError):  # fmt: skip
            return False
        else:
            return bool(email_address and email_address.verified)

    @admin.display(description=_("Joined"), ordering="-date_joined")
    def date_joined_display(self, obj: CustomUser) -> str:
        """Format date joined for better readability."""
        return obj.date_joined.strftime("%Y-%m-%d %H:%M")

    @admin.display(description=_("Last Login"), ordering="-last_login")
    def last_login_display(self, obj: CustomUser) -> StrOrPromise:
        """Format last login date for better readability."""
        if obj.last_login:
            return obj.last_login.strftime("%Y-%m-%d %H:%M")
        return _("Never")

    def get_fieldsets(
        self,
        _: HttpRequest,
        obj: Any | None = None,
    ) -> Any:
        """Use different fieldsets for regular users vs superusers."""
        if not obj:
            # Use add_fieldsets when creating a new user
            return self.add_fieldsets

        # For existing users, use appropriate fieldset based on type
        if obj.is_superuser:
            return self.fieldsets
        return self.regular_user_fieldsets

    @admin.action(description=_("Mark selected users' emails as verified"))
    def verify_email(self, request: HttpRequest, queryset: QuerySet[CustomUser]) -> None:
        """Mark selected user emails as verified."""
        count = 0
        for user in queryset:
            email_addresses = EmailAddress.objects.filter(user=user, verified=False)
            count += email_addresses.update(verified=True)

        if count:
            messages.success(
                request,
                _("Successfully verified %(count)d email addresses.") % {"count": count},
            )
        else:
            messages.info(request, _("No unverified email addresses found."))

    @admin.action(description=_("Activate selected users"))
    def activate_users(self, request: HttpRequest, queryset: QuerySet[CustomUser]) -> None:
        """Activate selected users."""
        count = queryset.filter(is_active=False).update(is_active=True)
        if count:
            messages.success(
                request,
                _("Successfully activated %(count)d users.") % {"count": count},
            )
        else:
            messages.info(request, _("All selected users were already active."))

    @admin.action(description=_("Deactivate selected users"))
    def deactivate_users(
        self,
        request: HttpRequest,
        queryset: QuerySet[CustomUser],
    ) -> None:
        """Deactivate selected users."""
        # Don't allow deactivating your own account
        if request.user.pk in queryset.values_list("pk", flat=True):
            messages.error(request, _("You cannot deactivate your own account."))
            return

        count = queryset.filter(is_active=True).update(is_active=False)
        if count:
            messages.success(
                request,
                _("Successfully deactivated %(count)d users.") % {"count": count},
            )
        else:
            messages.info(request, _("All selected users were already inactive."))

    def save_model(self, request: HttpRequest, obj: CustomUser, form: Any, change: bool) -> None:  # noqa: FBT001
        """Save user and handle email verification and password logic."""
        creating_new_user = not change

        # Double-check that non-superusers don't have usable passwords
        if not obj.is_superuser and obj.has_usable_password():
            obj.set_unusable_password()
            messages.info(request, _("Password removed as user is not a superuser."))

        super().save_model(request, obj, form, change)

        # Create verified email address for new users
        if creating_new_user:
            EmailAddress.objects.get_or_create(
                user=obj,
                email=obj.email,
                defaults={"primary": True, "verified": True},
            )
