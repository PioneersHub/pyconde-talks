"""
Admin configuration for conference talk management.

This module defines the Django admin interfaces for the Speaker, Talk, Room, Streaming,
Question, QuestionVote, and Answer models.
"""

from datetime import timedelta
from typing import TYPE_CHECKING, ClassVar

from django.contrib import admin
from django.db.models import Count
from django.utils import timezone
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _

from .models import Rating, Room, Speaker, Streaming, Talk
from .models_qa import Answer, Question, QuestionVote


if TYPE_CHECKING:
    from django.db.models import QuerySet
    from django.http import HttpRequest
    from django_stubs_ext import StrOrPromise


# Constants
CONTENT_PREVIEW_LENGTH = 50


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
    def display_active_streaming(self, obj: Talk) -> StrOrPromise:
        """Display information about the active streaming for this talk's time slot."""
        if not obj.room or not obj.start_time:
            return str(_("No room or time scheduled"))

        margin = timedelta(minutes=1)
        min_duration = obj.duration / 2

        streaming = Streaming.objects.filter(
            room=obj.room,
            start_time__lte=obj.start_time + margin,
            end_time__gt=obj.start_time + min_duration,
        ).first()

        if streaming:
            return format_html(
                str(_('Streaming from {} to {} - <a href="{}" target="_blank">Video Link</a>')),
                timezone.localtime(streaming.start_time).strftime("%H:%M"),
                timezone.localtime(streaming.end_time).strftime("%H:%M %Z"),
                streaming.video_link,
            )

        return _("No active streaming found for this time slot")


class AnswerInline(admin.TabularInline[Answer, Question]):
    """Inline admin for Answer model."""

    model = Answer
    extra = 1
    fields = ("content", "user", "is_official", "created_at")
    readonly_fields = ("created_at",)


@admin.register(Question)
class QuestionAdmin(admin.ModelAdmin[Question]):
    """
    Admin configuration for the Question model.

    Attributes:
        list_display: Fields to display in the admin list view.
        list_filter: Fields to filter by in the admin list view.
        search_fields: Fields to search by in the admin list view.
        actions: Custom admin actions.
        readonly_fields: Fields that cannot be edited in the admin.
        inlines: Related models to display inline.
        list_select_related: Related models to prefetch for list view optimization.

    """

    list_display = (
        "content_preview",
        "talk",
        "display_name",
        "vote_count",
        "status",
        "has_answers",
        "created_at",
    )
    list_filter = ("status", "created_at", "talk__title")
    search_fields = ("content", "user__email", "user__first_name", "user__last_name", "talk__title")
    actions = ("approve_questions", "reject_questions", "mark_as_answered")
    readonly_fields = ("vote_count", "created_at", "updated_at")
    inlines = (AnswerInline,)
    list_select_related = ("talk", "user")

    fieldsets = (
        (
            None,
            {
                "fields": ("talk", "content", "status"),
            },
        ),
        (
            _("Author"),
            {
                "fields": ("user",),
            },
        ),
        (
            _("Metadata"),
            {
                "fields": ("vote_count", "created_at", "updated_at"),
                "classes": ("collapse",),
            },
        ),
    )

    @admin.display(description=_("Question"))
    def content_preview(self, obj: Question) -> str:
        """Display a preview of the question content."""
        if len(obj.content) > CONTENT_PREVIEW_LENGTH:
            return f"{obj.content[:CONTENT_PREVIEW_LENGTH]}..."
        return obj.content

    @admin.display(boolean=True, description=_("Has Answers"))
    def has_answers(self, obj: Question) -> bool:
        """Display whether the question has answers."""
        return obj.has_answer

    @admin.display(description=_("Votes"))
    def vote_count(self, obj: Question) -> int:
        """Display the number of votes for this question."""
        return obj.vote_count

    @admin.action(description=_("Reject selected questions (hide from public)"))
    def reject_questions(self, request: HttpRequest, queryset: QuerySet[Question]) -> None:
        """Mark selected questions as rejected, hiding them from public view."""
        # Use bulk update for efficiency
        updated = queryset.update(status=Question.Status.REJECTED, updated_at=timezone.now())
        self.message_user(
            request,
            _("%(count)d question(s) have been rejected.") % {"count": updated},
        )

    @admin.action(description=_("Mark selected questions as answered"))
    def mark_as_answered(self, request: HttpRequest, queryset: QuerySet[Question]) -> None:
        """Mark selected questions as answered."""
        updated = queryset.update(status=Question.Status.ANSWERED, updated_at=timezone.now())
        self.message_user(
            request,
            _("%(count)d question(s) have been marked as answered.") % {"count": updated},
        )

    @admin.action(description=_("Approve selected questions"))
    def approve_questions(self, request: HttpRequest, queryset: QuerySet[Question]) -> None:
        """Mark selected questions as approved."""
        updated = queryset.update(status=Question.Status.APPROVED, updated_at=timezone.now())
        self.message_user(
            request,
            _("%(count)d question(s) have been approved.") % {"count": updated},
        )


@admin.register(QuestionVote)
class QuestionVoteAdmin(admin.ModelAdmin[QuestionVote]):
    """
    Admin configuration for the QuestionVote model.

    Attributes:
        list_display: Fields to display in the admin list view.
        list_filter: Fields to filter by in the admin list view.
        search_fields: Fields to search by in the admin list view.
        readonly_fields: Fields that cannot be edited in the admin.

    """

    list_display = ("question_preview", "user", "created_at")
    list_filter = ("created_at",)
    search_fields = ("question__content", "user__email")
    readonly_fields = ("created_at",)

    @admin.display(description=_("Question"))
    def question_preview(self, obj: QuestionVote) -> str:
        """Display a preview of the question content."""
        content = obj.question.content
        if len(content) > CONTENT_PREVIEW_LENGTH:
            return f"{content[:CONTENT_PREVIEW_LENGTH]}..."
        return content


@admin.register(Answer)
class AnswerAdmin(admin.ModelAdmin[Answer]):
    """
    Admin configuration for the Answer model.

    Attributes:
        list_display: Fields to display in the admin list view.
        list_filter: Fields to filter by in the admin list view.
        search_fields: Fields to search by in the admin list view.
        readonly_fields: Fields that cannot be edited in the admin.
        list_select_related: Related models to prefetch for list view optimization.

    """

    list_display = ("content_preview", "question_preview", "user", "is_official", "created_at")
    list_filter = ("is_official", "created_at")
    search_fields = ("content", "question__content", "user__email")
    readonly_fields = ("created_at", "updated_at")
    list_select_related = ("question", "user")

    fieldsets = (
        (
            None,
            {
                "fields": ("question", "content", "user", "is_official"),
            },
        ),
        (
            _("Metadata"),
            {
                "fields": ("created_at", "updated_at"),
                "classes": ("collapse",),
            },
        ),
    )

    @admin.display(description=_("Answer"))
    def content_preview(self, obj: Answer) -> str:
        """Display a preview of the answer content."""
        if len(obj.content) > CONTENT_PREVIEW_LENGTH:
            return f"{obj.content[:CONTENT_PREVIEW_LENGTH]}..."
        return obj.content

    @admin.display(description=_("Question"))
    def question_preview(self, obj: Answer) -> str:
        """Display a preview of the question content."""
        content = obj.question.content
        if len(content) > CONTENT_PREVIEW_LENGTH:
            return f"{content[:CONTENT_PREVIEW_LENGTH]}..."
        return content


@admin.register(Rating)
class RatingAdmin(admin.ModelAdmin[Rating]):
    """
    Admin configuration for the Rating model.

    Displays ratings with their comments (visible only to admins).

    Attributes:
        list_display: Fields to display in the admin list view.
        list_filter: Fields to filter by in the admin list view.
        search_fields: Fields to search by in the admin list view.
        readonly_fields: Fields that cannot be edited in the admin.
        list_select_related: Related models to prefetch for list view optimization.

    """

    list_display = ("talk", "user", "score", "has_comment", "created_at")
    list_filter = ("score", "created_at")
    search_fields = ("talk__title", "user__email", "comment")
    readonly_fields = ("created_at", "updated_at")
    list_select_related = ("talk", "user")

    fieldsets = (
        (
            None,
            {
                "fields": ("talk", "user", "score", "comment"),
            },
        ),
        (
            _("Metadata"),
            {
                "fields": ("created_at", "updated_at"),
                "classes": ("collapse",),
            },
        ),
    )

    @admin.display(boolean=True, description=_("Has Comment"))
    def has_comment(self, obj: Rating) -> bool:
        """Display whether the rating has a comment."""
        return bool(obj.comment)
