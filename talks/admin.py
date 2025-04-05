"""
Admin configuration for conference talk management.

This module defines the Django admin interfaces for the Speaker and Talk models.
"""

from django.contrib import admin
from django.db.models import QuerySet
from django.http import HttpRequest
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _

from .models import Speaker, Talk


@admin.register(Speaker)
class SpeakerAdmin(admin.ModelAdmin):
    """
    Admin configuration for the Speaker model.

    Attributes:
        list_display: Fields to display in the admin list view.
        list_filter: Fields to filter by in the admin list view.
        search_fields: Fields to search by in the admin list view.
        fieldsets: Field groupings for the admin form.

    """

    list_display = ("name", "display_avatar", "gender", "pronouns")
    list_filter = ("gender",)
    search_fields = ("name", "biography", "pronouns")

    fieldsets = (
        (
            None,
            {
                "fields": ("name", "biography", "avatar"),
            },
        ),
        (
            _("Personal Information"),
            {
                "fields": ("gender", "gender_self_description", "pronouns"),
            },
        ),
    )

    @admin.display(description=_("Avatar"))
    def display_avatar(self, obj: Speaker) -> str | None:
        """Display a thumbnail of the speaker's avatar in the admin list view."""
        if obj.avatar:
            return format_html(
                '<img src="{}" width="50" height="50" style="border-radius: 50%;" alt="{}" />',
                obj.avatar,
                _("Avatar of {}").format(obj.name),
            )
        return "-"


@admin.register(Talk)
class TalkAdmin(admin.ModelAdmin):
    """
    Admin configuration for the Talk model.

    Attributes:
        list_display: Fields to display in the admin list view.
        list_filter: Fields to filter by in the admin list view.
        search_fields: Fields to search by in the admin list view.
        date_hierarchy: Field to use for date-based navigation.
        filter_horizontal: Many-to-many fields to display with a horizontal filter interface.
        fieldsets: Field groupings for the admin form.
        readonly_fields: Fields that cannot be edited in the admin.

    """

    list_display = (
        "title",
        "presentation_type",
        "speaker_names",
        "date_time",
        "duration",
        "room",
        "track",
        "is_upcoming",
        "hide",
    )
    list_filter = ("presentation_type", "room", "track", "hide", "date_time")
    search_fields = ("title", "abstract", "description", "speakers__name")
    date_hierarchy = "date_time"
    filter_horizontal = ("speakers",)

    fieldsets = (
        (
            None,
            {
                "fields": ("presentation_type", "title", "speakers", "abstract", "description"),
            },
        ),
        (
            _("Scheduling"),
            {
                "fields": ("date_time", "duration", "room", "track"),
            },
        ),
        (
            _("Media"),
            {
                "fields": ("external_image_url", "image", "display_image_preview"),
            },
        ),
        (
            _("Links"),
            {
                "fields": ("pretalx_link", "slido_link", "video_link", "video_start_time"),
            },
        ),
        (
            _("Settings"),
            {
                "fields": ("hide", "created_at", "updated_at"),
                "classes": ("collapse",),
            },
        ),
    )

    readonly_fields = ("created_at", "updated_at", "display_image_preview")

    def get_queryset(self, request: HttpRequest) -> QuerySet[Talk]:
        """Prefetch related speakers to optimize database queries."""
        return super().get_queryset(request).prefetch_related("speakers")

    @admin.display(description=_("Image Preview"))
    def display_image_preview(self, obj: Talk) -> str | None:
        """Display a preview of the talk's image."""
        if obj.image:
            return format_html(
                '<img src="{}" width="200" height="150" alt="{}" />',
                obj.image.url,
                _("Preview of {}").format(obj.title),
            )
        if obj.external_image_url:
            return format_html(
                '<img src="{}" width="200" height="150" alt="{}" />',
                obj.external_image_url,
                _("Preview of {}").format(obj.title),
            )
        return "-"

    @admin.display(boolean=True, description=_("Upcoming"))
    def is_upcoming(self, obj: Talk) -> bool:
        """Display whether the talk is upcoming."""
        return obj.is_upcoming()
