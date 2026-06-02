"""Admin interface for users."""

from typing import TYPE_CHECKING, Any, ClassVar

from allauth.account.models import EmailAddress
from django.contrib import admin, messages
from django.contrib.auth.admin import UserAdmin
from django.db.models import Prefetch
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect
from django.shortcuts import render
from django.urls import URLPattern, path, reverse
from django.utils.translation import gettext_lazy as _

from .forms import CustomUserChangeForm, RegularUserCreationForm, SuperUserCreationForm
from .models import CustomUser, Ticket


if TYPE_CHECKING:
    from django.db.models.query import QuerySet
    from django.forms import ModelForm
    from django_stubs_ext import StrOrPromise


class EmailVerificationListFilter(admin.SimpleListFilter):
    """Filter users by email verification status."""

    title = _("Email verification")
    parameter_name = "verified"

    def lookups(
        self,
        request: HttpRequest,  # noqa: ARG002
        model_admin: Any,  # noqa: ARG002
    ) -> list[tuple[str, StrOrPromise]]:
        """Return filter options."""
        return [
            ("yes", _("Verified")),
            ("no", _("Not verified")),
        ]

    def queryset(
        self,
        request: HttpRequest,  # noqa: ARG002
        queryset: QuerySet[CustomUser],
    ) -> QuerySet[CustomUser]:
        """Filter queryset based on selected option."""
        if self.value() == "yes":
            return queryset.filter(emailaddress__verified=True)
        if self.value() == "no":
            return queryset.filter(emailaddress__verified=False)
        return queryset


class TicketInline(admin.TabularInline[Ticket, CustomUser]):
    """Inline admin interface for Ticket objects."""

    model = Ticket
    extra = 1
    fields: ClassVar[tuple[str, ...]] = ("ticket_id", "event", "created_at")
    readonly_fields: ClassVar[list[str]] = ["created_at"]
    autocomplete_fields: ClassVar[tuple[str, ...]] = ("event",)


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
        "ticket_ids",
        "event_names",
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

    # Sections shared by superuser and regular-user edit layouts. The only difference between the
    # two is whether the top section includes the password field, so keep everything below
    # ("Personal info" through "Important dates") in one tuple and prepend the right header
    # section below.
    _shared_edit_sections: ClassVar[tuple[tuple[Any, dict[str, Any]], ...]] = (
        (_("Personal info"), {"fields": ("first_name", "last_name")}),
        (
            _("Events"),
            {
                "fields": ("events",),
                "description": _("Events this user has access to."),
            },
        ),
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

    # Override UserAdmin fieldsets to use email instead of username (superuser edit view)
    fieldsets: ClassVar[list[Any]] = [
        (None, {"fields": ("email", "password")}),
        *_shared_edit_sections,
    ]

    # Field layout for regular users in edit mode (no password)
    regular_user_fieldsets: ClassVar[tuple[Any, ...]] = (
        (None, {"fields": ("email",)}),
        *_shared_edit_sections,
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
    filter_horizontal = ("events",)
    inlines = (EmailAddressInline, TicketInline)
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

        # Handle POST data for superusers
        user_type = request.GET.get("user_type")
        if request.method == "POST" and user_type == "super":
            post_data = request.POST.copy()
            post_data["is_superuser"] = "on"
            request.POST = post_data  # type: ignore[assignment]

        return super().add_view(request, form_url, extra_context)

    def get_form(  # type: ignore[override]
        self,
        request: HttpRequest,
        obj: CustomUser | None = None,
        change: bool = False,  # noqa: FBT001, FBT002
        **kwargs: Any,
    ) -> type[ModelForm[CustomUser]]:
        """Return the appropriate form class based on user type."""
        if obj is None:
            # Creating a new user — pick form based on the query parameter
            user_type = request.GET.get("user_type")
            if user_type == "regular":
                kwargs["form"] = RegularUserCreationForm
            else:
                kwargs["form"] = SuperUserCreationForm
        return super().get_form(request, obj, change=change, **kwargs)

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
        """Optimize query by prefetching relations used in the changelist."""
        queryset = super().get_queryset(request)
        # ``events`` is hit per-row by the ``event_names`` column - without the prefetch
        # the changelist runs one query per user.
        return queryset.prefetch_related(
            "emailaddress_set",
            "groups",
            "events",
            Prefetch(
                "tickets",
                queryset=Ticket.objects.select_related("event").order_by("ticket_id"),
            ),
        )

    @admin.display(description=_("Full Name"))
    def full_name(self, obj: CustomUser) -> str:
        """Display the user's full name."""
        return f"{obj.first_name} {obj.last_name}".strip() or "-"

    @admin.display(description=_("Tickets"))
    def ticket_ids(self, obj: CustomUser) -> str:
        """Display ticket IDs assigned to the user."""
        ticket_ids = sorted(ticket.ticket_id for ticket in obj.tickets.all())
        return ", ".join(ticket_ids) if ticket_ids else "-"

    @admin.display(description=_("Events"))
    def event_names(self, obj: CustomUser) -> str:
        """
        Display the events the user has access to.

        Reads from the prefetched ``events`` cache to keep the changelist O(1) per row.
        """
        names = sorted(e.name for e in obj.events.all())
        return ", ".join(names) if names else "-"

    @admin.display(boolean=True, description=_("Email Verified"))
    def email_verified(self, obj: CustomUser) -> bool:
        """
        Return True when the user has at least one verified email address.

        The list view prefetches ``emailaddress_set``; walk the cache instead of
        re-querying so the column does not add a query per row.
        """
        return any(ea.verified for ea in obj.emailaddress_set.all())

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
        request: HttpRequest,
        obj: Any | None = None,
    ) -> Any:
        """Use different fieldsets for regular users vs superusers."""
        if not obj:
            # Creating a new user — pick fieldsets based on the query parameter
            user_type = request.GET.get("user_type")
            if user_type == "regular":
                return self.regular_user_add_fieldsets
            return self.superuser_add_fieldsets

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


@admin.register(Ticket)
class TicketAdmin(admin.ModelAdmin[Ticket]):
    """Admin configuration for the Ticket model."""

    list_display = ("ticket_id", "user", "event", "created_at")
    list_filter = ("event",)
    search_fields = ("ticket_id", "user__email")
    readonly_fields: ClassVar[list[str]] = ["created_at"]
    # The ``user`` and ``event`` columns dereference related rows;
    # without this the changelist runs an extra SELECT per row.
    list_select_related = ("user", "event")
    autocomplete_fields: ClassVar[tuple[str, ...]] = ("user", "event")
    list_per_page = 25
