"""Admin interface for events."""

from typing import TYPE_CHECKING, Any, ClassVar

from django import forms
from django.contrib import admin
from django.contrib.admin.widgets import FilteredSelectMultiple
from django.utils.translation import gettext_lazy as _

from users.models import CustomUser

from .models import Event


if TYPE_CHECKING:
    from collections.abc import Sequence


class EventAdminForm(forms.ModelForm[Event]):
    """Admin form for Event that exposes the reverse ``users`` relation."""

    users = forms.ModelMultipleChoiceField(
        queryset=CustomUser.objects.order_by("email"),
        required=False,
        widget=FilteredSelectMultiple(verbose_name=_("users"), is_stacked=False),
        label=_("Users with access"),
        help_text=_(
            "Move users to the right to grant access, to the left to revoke it. "
            "Use the filter box to search.",
        ),
    )

    class Meta:
        """Meta class for EventAdminForm."""

        model = Event
        fields = (
            "name",
            "slug",
            "year",
            "is_active",
            "show_rating_summary",
            "validation_api_url",
            "main_website_url",
            "imprint_url",
            "code_of_conduct_url",
            "privacy_policy_url",
            "venue_url",
            "transcriptions_url",
            "logo_svg_name",
            "made_by_name",
            "made_by_url",
            "pretalx_url",
        )

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Pre-populate the users field with the event's current users."""
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            self.fields["users"].initial = self.instance.users.all()

    def _save_m2m(self) -> None:
        """Persist the reverse users relation alongside the standard M2M save."""
        super()._save_m2m()  # type: ignore[misc]
        self.instance.users.set(self.cleaned_data.get("users", []))


@admin.register(Event)
class EventAdmin(admin.ModelAdmin[Event]):
    """Admin configuration for the Event model."""

    form = EventAdminForm
    list_display = (
        "name",
        "slug",
        "year",
        "is_active",
        "show_rating_summary",
        "validation_api_url_set",
    )
    list_filter = ("is_active", "show_rating_summary")
    search_fields = ("name", "slug")
    prepopulated_fields: ClassVar[dict[str, Sequence[str]]] = {"slug": ("name",)}
    fieldsets: ClassVar[list[Any]] = [
        (
            None,
            {
                "fields": (
                    "name",
                    "slug",
                    "year",
                    "is_active",
                    "show_rating_summary",
                    "validation_api_url",
                ),
            },
        ),
        (
            _("Users with access"),
            {
                "fields": ("users",),
                "description": _(
                    "Grant or revoke access for multiple users at once. "
                    "Changes are saved when the event is saved.",
                ),
            },
        ),
        (
            _("Branding"),
            {
                "fields": (
                    "main_website_url",
                    "imprint_url",
                    "code_of_conduct_url",
                    "privacy_policy_url",
                    "venue_url",
                    "transcriptions_url",
                    "logo_svg_name",
                    "made_by_name",
                    "made_by_url",
                ),
            },
        ),
        (
            _("Pretalx"),
            {"fields": ("pretalx_url",)},
        ),
    ]

    @admin.display(boolean=True, description=_("API configured"))
    def validation_api_url_set(self, obj: Event) -> bool:
        """Show whether the validation API URL is configured."""
        return bool(obj.validation_api_url)
