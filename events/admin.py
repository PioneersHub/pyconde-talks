"""Admin interface for events."""

from typing import TYPE_CHECKING, Any, ClassVar

from django.contrib import admin

from .models import Event


if TYPE_CHECKING:
    from collections.abc import Sequence


@admin.register(Event)
class EventAdmin(admin.ModelAdmin[Event]):
    """Admin configuration for the Event model."""

    list_display = (
        "name",
        "slug",
        "year",
        "is_active",
        "validation_api_url_set",
    )
    list_filter = ("is_active",)
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
                    "validation_api_url",
                ),
            },
        ),
        (
            "Branding",
            {
                "fields": (
                    "main_website_url",
                    "venue_url",
                    "logo_svg_name",
                    "made_by_name",
                    "made_by_url",
                ),
            },
        ),
        (
            "Pretalx",
            {"fields": ("pretalx_url",)},
        ),
    ]

    @admin.display(boolean=True, description="API configured")
    def validation_api_url_set(self, obj: Event) -> bool:
        """Show whether the validation API URL is configured."""
        return bool(obj.validation_api_url)
