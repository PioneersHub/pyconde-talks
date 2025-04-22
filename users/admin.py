"""Admin interface for users."""

from typing import Any, ClassVar

from allauth.account.models import EmailAddress
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from django.db.models.query import QuerySet
from django.http import HttpRequest
from django.utils.translation import gettext_lazy as _

from .models import CustomUser


class EmailAddressInline(admin.TabularInline):
    """Inline admin interface for EmailAddress objects."""

    model = EmailAddress
    extra: int = 0
    readonly_fields: ClassVar[list[str]] = ["email", "verified", "primary"]
    can_delete: bool = False

    def has_add_permission(self, request: HttpRequest, obj: CustomUser | None = None) -> bool:  # noqa: ARG002
        """Prevent adding email addresses directly through admin."""
        return False


@admin.register(CustomUser)
class CustomUserAdmin(UserAdmin):
    """Admin configuration for the CustomUser model."""

    list_display: ClassVar[tuple[str, ...]] = (
        "email",
        "first_name",
        "last_name",
        "is_staff",
        "is_active",
        "date_joined",
    )
    list_filter: ClassVar[tuple[str, ...]] = (
        "is_staff",
        "is_superuser",
        "is_active",
        "date_joined",
    )
    search_fields: ClassVar[tuple[str, ...]] = ("email", "first_name", "last_name")
    ordering: ClassVar[tuple[str, ...]] = ("email",)

    fieldsets: ClassVar[tuple[tuple[str | None, dict], ...]] = (
        (None, {"fields": ("email", "password")}),
        (_("Personal info"), {"fields": ("first_name", "last_name")}),
        (
            _("Permissions"),
            {
                "fields": ("is_active", "is_staff", "is_superuser", "groups", "user_permissions"),
            },
        ),
        (_("Important dates"), {"fields": ("last_login", "date_joined")}),
    )

    add_fieldsets: ClassVar[tuple[tuple[str | None, dict], ...]] = (
        (
            None,
            {
                "classes": ("wide",),
                "fields": (
                    "email",
                    "password1",
                    "password2",
                    "is_staff",
                    "is_superuser",
                    "is_active",
                ),
            },
        ),
    )

    readonly_fields: ClassVar[tuple[str, ...]] = ("date_joined", "last_login")
    inlines: ClassVar[list[admin.TabularInline]] = [EmailAddressInline]

    def get_queryset(self, request: HttpRequest) -> QuerySet:
        """Optimize query by prefetching related email addresses."""
        queryset = super().get_queryset(request)
        return queryset.prefetch_related("emailaddress_set")

    def save_model(self, request: HttpRequest, obj: CustomUser, form: Any, change: Any) -> None:  # noqa: ANN401
        """Save model."""
        super().save_model(request, obj, form, change)
