"""
Admin configuration for core talk models (Room, Streaming, Speaker, Talk).

Q&A admins live in ``talks.admin_qa``, and rating/saved admins in ``talks.admin_rating``.
"""

from typing import TYPE_CHECKING, Any, ClassVar

from django.contrib import admin
from django.db.models import Avg, Count, Exists, OuterRef
from django.utils import timezone
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _

from .models import Rating, Room, Speaker, Streaming, Talk


if TYPE_CHECKING:
    from django.db.models import QuerySet
    from django.http import HttpRequest
    from django_stubs_ext import StrOrPromise


# Keep Q&A, rating, and Pretalx admin modules imported so Django's autodiscovery
# registers them.
import talks.admin_pretalx as _admin_pretalx  # noqa: F401
import talks.admin_qa as _admin_qa  # noqa: F401
import talks.admin_rating as _admin_rating  # noqa: F401


class TalkHasRatingCommentsFilter(admin.SimpleListFilter):
    """Filter talks by whether any of their ratings carries a comment."""

    title = _("Rating comments")
    parameter_name = "has_rating_comments"

    def lookups(
        self,
        request: HttpRequest,  # noqa: ARG002
        model_admin: Any,  # noqa: ARG002
    ) -> list[tuple[str, StrOrPromise]]:
        """Return the two-choice filter options."""
        return [
            ("yes", _("With comments")),
            ("no", _("Without comments")),
        ]

    def queryset(
        self,
        request: HttpRequest,  # noqa: ARG002
        queryset: QuerySet[Talk],
    ) -> QuerySet[Talk]:
        """Keep only talks that have (or don't have) at least one rating with a comment."""
        commented = Rating.objects.filter(talk=OuterRef("pk")).exclude(comment="")
        if self.value() == "yes":
            return queryset.filter(Exists(commented))
        if self.value() == "no":
            return queryset.filter(~Exists(commented))
        return queryset


@admin.register(Room)
class RoomAdmin(admin.ModelAdmin[Room]):
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

    fieldsets: ClassVar[list[Any]] = [
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
    ]

    def get_queryset(self, request: HttpRequest) -> QuerySet[Room]:
        """Annotate queryset with counts to avoid N+1 queries."""
        return (
            super()
            .get_queryset(request)
            .annotate(
                _talk_count=Count("talks", distinct=True),
                _streaming_count=Count("streamings", distinct=True),
            )
        )

    @admin.display(description=_("Talk Count"), ordering="_talk_count")
    def talk_count(self, obj: Room) -> int:
        """Display the number of talks in this room."""
        return getattr(obj, "_talk_count", obj.talks.count())

    @admin.display(description=_("Streaming Count"), ordering="_streaming_count")
    def streaming_count(self, obj: Room) -> int:
        """Display the number of streaming sessions for this room."""
        return getattr(obj, "_streaming_count", obj.streamings.count())

    @admin.display(boolean=True, description=_("Has Slido"))
    def has_slido_link(self, obj: Room) -> bool:
        """Display whether the room has a Slido link."""
        return bool(obj.slido_link)


@admin.register(Streaming)
class StreamingAdmin(admin.ModelAdmin[Streaming]):
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
        "formatted_transcription_url",
    )
    list_filter = ("room", "start_time", "end_time")
    search_fields = ("room__name", "video_link")
    autocomplete_fields: ClassVar[list[str]] = ["room"]

    fieldsets: ClassVar[list[Any]] = [
        (
            None,
            {
                "fields": ("room", "start_time", "end_time"),
            },
        ),
        (
            _("Media"),
            {
                "fields": ("video_link", "transcription_url"),
            },
        ),
    ]

    @admin.display(description=_("Video Link"))
    def formatted_video_link(self, obj: Streaming) -> str:
        """Display a formatted link to the video."""
        if obj.video_link:
            return format_html(
                '<a href="{}" target="_blank">{}</a>',
                obj.video_link,
                _("Video Link"),
            )
        return "-"

    @admin.display(description=_("Transcription"))
    def formatted_transcription_url(self, obj: Streaming) -> str:
        """Display a formatted link to the transcription."""
        if obj.transcription_url:
            return format_html(
                '<a href="{}" target="_blank">{}</a>',
                obj.transcription_url,
                _("Transcription"),
            )
        return "-"


@admin.register(Speaker)
class SpeakerAdmin(admin.ModelAdmin[Speaker]):
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

    fieldsets: ClassVar[list[Any]] = [
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
    ]

    def get_queryset(self, request: HttpRequest) -> QuerySet[Speaker]:
        """Annotate queryset with talk count to avoid N+1 queries."""
        return super().get_queryset(request).annotate(_talk_count=Count("talks", distinct=True))

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

    @admin.display(description=_("Talk Count"), ordering="_talk_count")
    def talk_count(self, obj: Speaker) -> int:
        """Display the number of talks by this speaker."""
        return getattr(obj, "_talk_count", obj.talks.count())


@admin.register(Talk)
class TalkAdmin(admin.ModelAdmin[Talk]):
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
        "event",
        "is_upcoming",
        "has_video",
        "hide",
        "avg_rating",
        "num_ratings",
        "num_saves",
    )
    list_filter = (
        "event",
        "presentation_type",
        "room",
        "track",
        "hide",
        "start_time",
        TalkHasRatingCommentsFilter,
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

    fieldsets: ClassVar[list[Any]] = [
        (
            None,
            {
                "fields": (
                    "event",
                    "presentation_type",
                    "title",
                    "speakers",
                    "abstract",
                    "description",
                ),
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
                    "transcription_url",
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
    ]

    readonly_fields = (
        "created_at",
        "updated_at",
        "display_image_preview",
        "display_active_streaming",
    )

    def get_queryset(self, request: HttpRequest) -> QuerySet[Talk]:
        """Prefetch related speakers and room to optimize database queries."""
        return (
            super()
            .get_queryset(request)
            .prefetch_related("speakers")
            .select_related("room")
            .annotate(
                _average_rating=Avg("ratings__score"),
                _rating_count=Count("ratings", distinct=True),
                _saved_count=Count("saved_by", distinct=True),
            )
        )

    @admin.display(description=_("Avg Rating"), ordering="_average_rating")
    def avg_rating(self, obj: Talk) -> str:
        """Display the average rating for this talk."""
        avg = getattr(obj, "_average_rating", None)
        if avg is None:
            return "-"
        return f"{avg:.1f}"

    @admin.display(description=_("# Ratings"), ordering="_rating_count")
    def num_ratings(self, obj: Talk) -> int:
        """Display the number of ratings for this talk."""
        return getattr(obj, "_rating_count", 0)

    @admin.display(description=_("# Saves"), ordering="_saved_count")
    def num_saves(self, obj: Talk) -> int:
        """Display the number of users who saved/bookmarked this talk."""
        return getattr(obj, "_saved_count", 0)

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
    def display_active_streaming(self, obj: Talk) -> StrOrPromise:
        """Display information about the active streaming for this talk's time slot."""
        if not obj.room or not obj.start_time:
            return str(_("No room or time scheduled"))

        streaming = obj.get_streaming()
        if streaming:
            return format_html(
                str(_('Streaming from {} to {} - <a href="{}" target="_blank">Video Link</a>')),
                timezone.localtime(streaming.start_time).strftime("%H:%M"),
                timezone.localtime(streaming.end_time).strftime("%H:%M %Z"),
                streaming.video_link,
            )

        return _("No active streaming found for this time slot")
