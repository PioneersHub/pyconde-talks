"""
Admin configuration for conference talk management.

This module defines the Django admin interfaces for the Speaker, Talk, Room, and Streaming models.
"""

from datetime import timedelta
from typing import ClassVar

from django.contrib import admin
from django.db.models import QuerySet
from django.http import HttpRequest
from django.utils import timezone
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _

from .models import Room, Speaker, Streaming, Talk


@admin.register(Room)
class RoomAdmin(admin.ModelAdmin):
    """
    Admin configuration for the Room model.

    Attributes:
        list_display: Fields to display in the admin list view.
        list_filter: Fields to filter by in the admin list view.
        search_fields: Fields to search by in the admin list view.
        fieldsets: Field groupings for the admin form.

    """

    list_display = (
        "name",
        "capacity",
        "talk_count",
        "streaming_count",
        "has_slido_link",
    )
    search_fields = ("name", "description")

    fieldsets = (
        (
            None,
            {
                "fields": ("name", "description", "capacity"),
            },
        ),
        (
            _("Links"),
            {
                "fields": ("slido_link",),
            },
        ),
    )

    @admin.display(description=_("Talk Count"))
    def talk_count(self, obj: Room) -> int:
        """Display the number of talks in this room."""
        return obj.talks.count()

    @admin.display(description=_("Streaming Count"))
    def streaming_count(self, obj: Room) -> int:
        """Display the number of streaming sessions for this room."""
        return obj.streamings.count()

    @admin.display(boolean=True, description=_("Has Slido"))
    def has_slido_link(self, obj: Room) -> bool:
        """Display whether the room has a Slido link."""
        return bool(obj.slido_link)


@admin.register(Streaming)
class StreamingAdmin(admin.ModelAdmin):
    """
    Admin configuration for the Streaming model.

    Attributes:
        list_display: Fields to display in the admin list view.
        list_filter: Fields to filter by in the admin list view.
        search_fields: Fields to search by in the admin list view.
        fieldsets: Field groupings for the admin form.
        autocomplete_fields: Fields to enable autocomplete interface.

    """

    list_display = (
        "room",
        "start_time",
        "end_time",
        "formatted_video_link",
    )
    list_filter = ("room", "start_time", "end_time")
    search_fields = ("room__name", "video_link")
    autocomplete_fields: ClassVar[list[str]] = ["room"]

    fieldsets = (
        (
            None,
            {
                "fields": ("room", "start_time", "end_time"),
            },
        ),
        (
            _("Media"),
            {
                "fields": ("video_link",),
            },
        ),
    )

    @admin.display(description=_("Video Link"))
    def formatted_video_link(self, obj: Streaming) -> str:
        """Display a formatted link to the video."""
        if obj.video_link:
            return format_html('<a href="{}" target="_blank">Video Link</a>', obj.video_link)
        return "-"


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

    list_display = (
        "name",
        "display_avatar",
        "gender",
        "pronouns",
        "talk_count",
    )
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
        (
            _("Integration"),
            {
                "fields": ("pretalx_id",),
                "classes": ("collapse",),
            },
        ),
    )

    @admin.display(description=_("Avatar"))
    def display_avatar(self, obj: Speaker) -> str:
        """Display a thumbnail of the speaker's avatar in the admin list view."""
        if obj.avatar:
            return format_html(
                '<img src="{}" width="50" height="50" style="border-radius: 50%;" alt="{}" />',
                obj.avatar,
                _("Avatar of {}").format(obj.name),
            )
        return "-"

    @admin.display(description=_("Talk Count"))
    def talk_count(self, obj: Speaker) -> int:
        """Display the number of talks by this speaker."""
        return obj.talks.count()


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
        autocomplete_fields: Fields to enable autocomplete interface.

    """

    list_display = (
        "title",
        "presentation_type",
        "speaker_names",
        "start_time",
        "duration",
        "room_name",
        "track",
        "is_upcoming",
        "has_video",
        "hide",
    )
    list_filter = (
        "presentation_type",
        "room",
        "track",
        "hide",
        "start_time",
    )
    search_fields = (
        "title",
        "abstract",
        "description",
        "speakers__name",
        "room__name",
    )
    date_hierarchy = "start_time"
    filter_horizontal = ("speakers",)
    autocomplete_fields: ClassVar[list[str]] = ["room"]

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
                "fields": ("start_time", "duration", "room", "track"),
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
                "fields": (
                    "pretalx_link",
                    "slido_link",
                    "video_link",
                    "video_start_time",
                    "display_active_streaming",
                ),
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

    readonly_fields = (
        "created_at",
        "updated_at",
        "display_image_preview",
        "display_active_streaming",
    )

    def get_queryset(self, request: HttpRequest) -> QuerySet[Talk]:
        """Prefetch related speakers and room to optimize database queries."""
        return super().get_queryset(request).prefetch_related("speakers").select_related("room")

    @admin.display(description=_("Room"))
    def room_name(self, obj: Talk) -> str:
        """Display the room name or an empty string if no room is assigned."""
        return obj.room.name if obj.room else ""

    @admin.display(description=_("Image Preview"))
    def display_image_preview(self, obj: Talk) -> str:
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

    @admin.display(boolean=True, description=_("Has Video"))
    def has_video(self, obj: Talk) -> bool:
        """Display whether the talk has a video link."""
        return bool(obj.get_video_link())

    @admin.display(description=_("Active Streaming"))
    def display_active_streaming(self, obj: Talk) -> str:
        """Display information about the active streaming for this talk's time slot."""
        if not obj.room or not obj.start_time:
            return _("No room or time scheduled")

        margin = timedelta(minutes=1)
        min_duration = obj.duration / 2

        streaming = Streaming.objects.filter(
            room=obj.room,
            start_time__lte=obj.start_time + margin,
            end_time__gt=obj.start_time + min_duration,
        ).first()

        if streaming:
            return format_html(
                _('Streaming from {} to {} - <a href="{}" target="_blank">Video Link</a>'),
                timezone.localtime(streaming.start_time).strftime("%H:%M"),
                timezone.localtime(streaming.end_time).strftime("%H:%M %Z"),
                streaming.video_link,
            )

        return _("No active streaming found for this time slot")
