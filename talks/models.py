"""
Conference talk management module for the event talks site.

This module provides the Talk model for storing and managing conference talks, including their
metadata, scheduling information, and video links.
"""

from datetime import UTC, datetime, timedelta
from enum import IntEnum
from functools import cached_property
from typing import TYPE_CHECKING, Any, ClassVar, Self
from urllib.parse import urlparse

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Avg, Count, F, Q
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from talks.types import RatingStats, VideoProvider
from talks.validators import validate_video_link
from utils.url import add_query_param


if TYPE_CHECKING:
    from django_stubs_ext.db.models.manager import RelatedManager

    from events.models import Event
    from users.models import CustomUser


# Constants
EMPTY_TRACK_NAME = "No track"
LIGHTNING_TRACK_NAME = "Lightning Talks"
FAR_FUTURE = datetime(2050, 1, 1, 0, 0, 0, tzinfo=UTC)
MAX_PRETALX_ID_LENGTH = 50
MAX_PRONOUNS_LENGTH = 50
MAX_ROOM_NAME_LENGTH = 50
MAX_SPEAKER_NAME_LENGTH = 200
MAX_TALK_TITLE_LENGTH = 250
MAX_TRACK_NAME_LENGTH = 100


class Room(models.Model):
    """Represents a conference room where talks take place."""

    # Rooms are event-scoped: the same physical room reused across events is a separate
    # Room row per event. on_delete=PROTECT so an event with rooms can't be deleted out
    # from under its talks/streamings; you reassign or remove rooms explicitly first.
    # Required: every room belongs to an event (migrations 0024 add it nullable, 0025
    # backfill existing rows, 0027 tighten to NOT NULL).
    event = models.ForeignKey(
        "events.Event",
        on_delete=models.PROTECT,
        related_name="rooms",
        help_text=_("Event this room belongs to"),
    )

    # Unique per event (see Meta.constraints), not globally: the same room name can exist
    # under different events.
    name = models.CharField(
        max_length=MAX_ROOM_NAME_LENGTH,
        help_text=_("Name of the room"),
    )

    # Stable Pretalx room id (the integer ``slot.room.id``). Null for manually created or
    # legacy rooms; the importer stamps it lazily on the next sync. This is the match key
    # that lets a room renamed on Pretalx be renamed in place instead of duplicated.
    pretalx_id = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text=_("Stable Pretalx room id (slot.room.id); null for manual/legacy rooms"),
    )

    description = models.TextField(
        blank=True,
        help_text=_("Description of the room"),
    )

    capacity = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text=_("Maximum number of people that can fit in the room"),
    )

    slido_link = models.URLField(
        blank=True,
        default="",
        help_text=_("Link to Slido for this room"),
    )

    streamings: RelatedManager[Streaming]
    talks: RelatedManager[Talk]

    class Meta:
        """Metadata for the Room model."""

        verbose_name = _("Room")
        verbose_name_plural = _("Rooms")
        # Group by event then name so duplicate names across events don't interleave.
        ordering: ClassVar[list[str]] = ["event", "name"]
        constraints: ClassVar[list[models.BaseConstraint]] = [
            # Room names are unique within an event, not globally.
            models.UniqueConstraint(
                fields=["event", "name"],
                name="uniq_room_event_name",
            ),
            # At most one room per Pretalx id per event. Partial so legacy/manual rooms
            # (pretalx_id IS NULL) don't collide with each other.
            models.UniqueConstraint(
                fields=["event", "pretalx_id"],
                condition=Q(pretalx_id__isnull=False),
                name="uniq_room_event_pretalx_id",
            ),
        ]

    def __str__(self) -> str:
        """Return the room name."""
        return self.name

    @classmethod
    def resolve_for_event(
        cls,
        *,
        event: Event | None,
        pretalx_id: int | None,
        name: str,
    ) -> Room | None:
        """
        Find the local Room for a Pretalx room within an event, without writing.

        Match order: ``(event, pretalx_id)`` first (the stable key that survives a
        rename), then ``(event, name)`` for legacy rows that predate id-keying. Returns
        ``None`` on a miss. This is the single matcher shared by the importer and the
        apply step so both behave identically; it never creates or mutates a row.
        """
        if pretalx_id is not None:
            match = cls.objects.filter(event=event, pretalx_id=pretalx_id).first()
            if match is not None:
                return match
        if name:
            return cls.objects.filter(event=event, name=name).first()
        return None

    def is_streaming_live(self) -> bool:
        """Check if there's currently a live streaming for this room."""
        now = timezone.now()
        return Streaming.objects.filter(
            room=self,
            start_time__lte=now,
            end_time__gte=now,
        ).exists()


class Streaming(models.Model):
    """Represents a video streaming session for a room."""

    room = models.ForeignKey(
        Room,
        on_delete=models.CASCADE,
        related_name="streamings",
        help_text=_("Room where the streaming takes place"),
    )

    start_time = models.DateTimeField(
        help_text=_("When the streaming starts"),
    )

    end_time = models.DateTimeField(
        help_text=_("When the streaming ends"),
    )

    video_link = models.URLField(
        help_text=_("Link to the streaming"),
    )

    transcription_url = models.URLField(
        blank=True,
        default="",
        help_text=_("Link to the transcription page for talks in this streaming session."),
    )

    class Meta:
        """Metadata for the Streaming model."""

        verbose_name = _("Streaming")
        verbose_name_plural = _("Streamings")
        ordering: ClassVar[list[str]] = ["start_time"]
        indexes: ClassVar[list[models.Index]] = [
            models.Index(fields=["room", "start_time"]),
            models.Index(fields=["room", "end_time"]),
        ]
        constraints: ClassVar[list[models.CheckConstraint]] = [
            models.CheckConstraint(
                condition=models.Q(start_time__lt=models.F("end_time")),
                name="streaming_start_before_end",
            ),
        ]

    def __str__(self) -> str:
        """Return a string representation of the streaming."""
        local_start = timezone.localtime(self.start_time).strftime("%b %d, %Y %H:%M")
        local_end = timezone.localtime(self.end_time).strftime("%b %d, %Y %H:%M (%Z)")
        return f"Streaming for {self.room.name} from {local_start} to {local_end}"

    def clean(self) -> None:
        """Validate that this streaming doesn't overlap with another for the same room."""
        super().clean()
        if self.start_time and self.end_time:
            overlapping = Streaming.objects.filter(
                room=self.room,
                start_time__lt=self.end_time,
                end_time__gt=self.start_time,
            )

            # Exclude self when updating
            if self.pk:
                overlapping = overlapping.exclude(pk=self.pk)

            if overlapping.exists():
                raise ValidationError(
                    _("This streaming overlaps with another streaming for the same room."),
                )

    def is_active(self) -> bool:
        """Check if the streaming is happening now."""
        now = timezone.now()
        return self.start_time <= now <= self.end_time


class Speaker(models.Model):
    """Represents a conference speaker."""

    class Gender(models.TextChoices):
        """Enumeration of gender options available for speakers."""

        MAN = "M", _("Man")
        WOMAN = "W", _("Woman")
        NON_BINARY = "NB", _("Non-binary")
        GENDERQUEER = "GQ", _("Genderqueer")
        SELF_DESCRIBE = "SD", _("Self-describe")
        PREFER_NOT_TO_SAY = "NS", _("Prefer not to say")

    name = models.CharField(
        max_length=MAX_SPEAKER_NAME_LENGTH,
        help_text=_("Full name of the speaker"),
    )

    biography = models.TextField(
        blank=True,
        help_text=_("Biography of the speaker"),
    )

    avatar = models.URLField(
        blank=True,
        help_text=_("URL to the speaker's avatar image"),
    )

    gender = models.CharField(
        max_length=2,
        choices=Gender.choices,
        blank=True,
        help_text=_("Gender identity (optional)"),
    )

    gender_self_description = models.CharField(
        max_length=100,
        blank=True,
        help_text=_("If you selected 'Self-describe', please specify your gender identity"),
    )

    pronouns = models.CharField(
        max_length=MAX_PRONOUNS_LENGTH,
        blank=True,
        help_text=_("Preferred pronouns (e.g., he/him, she/her, they/them)"),
    )

    pretalx_id = models.CharField(
        max_length=MAX_PRETALX_ID_LENGTH,
        unique=True,
        help_text=_("Unique identifier for the speaker in the Pretalx system"),
    )

    talks: RelatedManager[Talk]

    class Meta:
        """Metadata for the Speaker model."""

        verbose_name = _("Speaker")
        verbose_name_plural = _("Speakers")
        ordering: ClassVar[list[str]] = ["name"]
        indexes: ClassVar[list[models.Index]] = [
            models.Index(fields=["pretalx_id"]),
        ]

    def __str__(self) -> str:
        """Return the speaker name."""
        return self.name


def _talk_image_upload_path(instance: Talk, filename: str) -> str:
    """Return the upload path for a talk image, using the event's assets sub-directory."""
    # Guard on event_id (no query, no RelatedObjectDoesNotExist): Talk.event is required,
    # so its accessor raises when unset - which can happen for an unsaved talk mid-creation.
    subdir = instance.event.slug if instance.event_id else ""
    if subdir:
        return f"talk_images/{subdir}/{filename}"
    return f"talk_images/{filename}"


class TalkQuerySet(models.QuerySet["Talk"]):  # type: ignore[call-arg]
    """Custom queryset for ``Talk`` with access-control helpers."""

    def accessible_to(self, user: CustomUser) -> Self:
        """
        Return talks the given user is allowed to see.

        Superusers see every talk. Any other user only sees talks whose event they have access
        to. Every talk belongs to an event (``Talk.event`` is required), so there is no
        event-less escape hatch.
        """
        if user.is_superuser:
            return self
        return self.filter(event__in=user.events.all())

    def with_streamings(self) -> list[Talk]:
        """
        Evaluate the queryset and batch-load the ``streaming`` cache on every row.

        Returns a list (not a queryset): like Django's own ``prefetch_related`` chain, this is a
        terminal operation - further filtering would invalidate the cache.  Use it in views where
        you would otherwise call ``list(qs)`` and then iterate each talk's ``get_video_link`` /
        ``get_transcription_url`` / ``streaming``.
        """
        talks = list(self)
        prefetch_streamings(talks)
        return talks

    def with_rating_stats(self) -> Self:
        """
        Annotate each talk with ``average_rating`` and ``rating_count``.

        Centralizes the per-row aggregate used by the talk list, upcoming-talks
        partial, and admin so the annotation lives in one place and templates can
        rely on the same attribute names everywhere.
        """
        return self.annotate(
            average_rating=Avg("ratings__score"),
            rating_count=Count("ratings"),
        )


class Talk(models.Model):
    """Represents a conference talk."""

    class PresentationType(models.TextChoices):
        """Enumeration of presentation types."""

        KEYNOTE = "Keynote", _("Keynote")
        KIDS = "Kids", _("Kids")
        LIGHTNING = "Lightning", _("Lightning Talk")
        OPEN = "Open Space", _("Open Space")
        PANEL = "Panel", _("Panel")
        PLENARY = "Plenary", _("Plenary")
        TALK = "Talk", _("Talk")
        TUTORIAL = "Tutorial", _("Tutorial")

    DEFAULT_DURATIONS: ClassVar[dict[str, timedelta]] = {
        PresentationType.KEYNOTE: timedelta(minutes=45),
        PresentationType.KIDS: timedelta(minutes=30),
        PresentationType.PANEL: timedelta(minutes=45),
        PresentationType.TALK: timedelta(minutes=30),
        PresentationType.TUTORIAL: timedelta(minutes=45),
    }

    presentation_type = models.CharField(
        max_length=10,
        choices=PresentationType.choices,
        default=PresentationType.TALK,
        help_text=_("Type of the presentation"),
    )
    title = models.CharField(
        max_length=MAX_TALK_TITLE_LENGTH,
        help_text=_("Title of the talk"),
    )
    speakers: models.ManyToManyField[Speaker, Speaker] = models.ManyToManyField(
        Speaker,
        related_name="talks",
        help_text=_("Speakers giving this talk"),
    )
    abstract = models.TextField(
        blank=True,
        help_text=_("Talk abstract"),
    )
    description = models.TextField(
        blank=True,
        help_text=_("Full description of the talk"),
    )
    start_time = models.DateTimeField(
        blank=True,
        default=FAR_FUTURE,
        help_text=_("Date and time when the talk is scheduled"),
    )
    duration = models.DurationField(
        blank=True,
        default=timedelta(),
        help_text=_("Duration of the talk"),
    )
    # Derived as ``start_time + duration`` by the database (Django 5+ GeneratedField).
    # Stored (``db_persist=True``) so it is indexable and reusable in queries without
    # an ``ExpressionWrapper`` annotation. Updates automatically when start_time or
    # duration change - never set this column directly.
    end_time = models.GeneratedField(
        expression=F("start_time") + F("duration"),
        output_field=models.DateTimeField(),
        db_persist=True,
        help_text=_("Computed talk end time (start_time + duration). Managed by the database."),
    )
    room = models.ForeignKey(
        Room,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="talks",
        help_text=_("Room where the talk takes place"),
    )
    track = models.CharField(
        max_length=MAX_TRACK_NAME_LENGTH,
        blank=True,
        default=EMPTY_TRACK_NAME,
        help_text=_("Track or category of the talk"),
    )
    external_image_url = models.URLField(
        blank=True,
        default="",
        help_text=_("URL to an externally hosted image"),
    )
    image = models.ImageField(
        upload_to=_talk_image_upload_path,
        blank=True,
        null=True,
        help_text=_("Image for the talk. Overrides the external image URL if provided."),
    )
    pretalx_link = models.URLField(
        blank=True,
        default="",
        help_text=_("Link to talk description in pretalx"),
    )
    slido_link = models.URLField(
        blank=True,
        default="",
        help_text=_("Link to questions on Slido. Overrides the room's link if provided."),
    )
    video_link = models.URLField(
        validators=[validate_video_link],
        blank=True,
        default="",
        help_text=_("Link to talk recording. Overrides the calculated streaming link if provided."),
    )
    transcription_url = models.URLField(
        blank=True,
        default="",
        help_text=_("Link to external transcription page for this talk."),
    )
    video_start_time = models.PositiveIntegerField(
        blank=True,
        null=True,
        help_text=_("Start time in seconds"),
    )
    # Required: every talk belongs to an event (migration 0028 backfills existing rows,
    # 0029 tightens to NOT NULL). on_delete=CASCADE: deleting an event removes its talks.
    event = models.ForeignKey(
        "events.Event",
        on_delete=models.CASCADE,
        related_name="talks",
        help_text=_("Event this talk belongs to"),
    )
    hide = models.BooleanField(
        default=False,
        help_text=_("Hide this talk from the public"),
    )
    created_at = models.DateTimeField(
        default=timezone.now,
        help_text=_("When this talk was added to the system"),
    )
    updated_at = models.DateTimeField(
        auto_now=True,
        help_text=_("When this talk was last modified"),
    )

    ratings: RelatedManager[Rating]

    objects: ClassVar[TalkQuerySet] = TalkQuerySet.as_manager()  # type: ignore[assignment]

    class Meta:
        """Metadata options for the Talk model."""

        ordering: ClassVar[list[str]] = ["start_time"]
        verbose_name = _("Talk")
        verbose_name_plural = _("Talks")
        indexes: ClassVar[list[models.Index]] = [
            models.Index(fields=["start_time"]),
            models.Index(fields=["room"]),
            models.Index(fields=["event"]),
            # Composite indexes for common query patterns
            models.Index(fields=["room", "start_time"]),
            models.Index(fields=["hide", "start_time"]),
            models.Index(fields=["presentation_type", "start_time"]),
            models.Index(fields=["event", "start_time"]),
            # Speeds up the "current"/"completed" status filter and Streaming overlap checks.
            models.Index(fields=["end_time"]),
            models.Index(fields=["room", "end_time"]),
        ]

    def __str__(self) -> str:
        """Return a string representation of the talk."""
        return f"{self.title} by {self.speaker_names}"

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Save the talk, filling in derived field defaults first."""
        self.apply_derived_defaults()
        super().save(*args, **kwargs)

    def apply_derived_defaults(self) -> None:
        """
        Populate fields that are derived from other data before persisting.

        Kept separate from ``save()`` so the defaulting rules can be unit tested without a database
        round-trip and reused by importers that build talks in bulk.
        """
        if not self.duration:
            self.duration = self.default_duration()
        if not self.track:
            self.track = self.default_track()
        self.video_link = self._enrich_video_link()

    def default_duration(self) -> timedelta:
        """Return the default duration for this talk's presentation type."""
        return self.DEFAULT_DURATIONS.get(self.presentation_type, timedelta())

    def default_track(self) -> str:
        """Return the default track name for this talk's presentation type."""
        if self.presentation_type == self.PresentationType.LIGHTNING:
            return LIGHTNING_TRACK_NAME
        return EMPTY_TRACK_NAME

    def clean(self) -> None:
        """Validate room/event coherence and that the talk doesn't overlap in its room."""
        super().clean()

        # A talk's room must belong to the same event as the talk (rooms are event-scoped).
        # Guarded on event_id so it stays correct while a talk is mid-edit without an event.
        if self.room is not None and self.event_id and self.room.event_id != self.event_id:
            raise ValidationError(
                _("The selected room belongs to a different event than this talk."),
            )

        if not self.room or not self.start_time or not self.duration:
            return

        if self.has_room_conflict(
            self.room,
            self.start_time,
            self.duration,
            exclude_pk=self.pk,
        ):
            raise ValidationError(
                _("This talk overlaps with another talk in the same room."),
            )

    @classmethod
    def has_room_conflict(
        cls,
        room: Room,
        start_time: datetime,
        duration: timedelta,
        *,
        exclude_pk: int | None = None,
    ) -> bool:
        """
        Return True if a talk in ``room`` overlaps the proposed time window.

        Two talks overlap when one starts before the other ends **and** ends
        after the other starts.  The check is performed against persisted
        ``Talk`` rows so it is safe to call before ``save()``.
        """
        if not room or not start_time or not duration:
            return False

        end_time = start_time + duration
        qs = cls.objects.filter(
            room=room,
            start_time__lt=end_time,
            end_time__gt=start_time,
        )
        if exclude_pk is not None:
            qs = qs.exclude(pk=exclude_pk)
        return qs.exists()

    def _enrich_video_link(self) -> str:
        """
        Add provider-specific query parameters to the video link.

        Only operates on ``self.video_link`` (the stored field), not on streaming fallback URLs.
        The method is idempotent: the parameter is only added when it is not already present.
        """
        if not self.video_link:
            return ""

        link_lower = self.video_link.lower()
        is_youtube = any(
            provider.value in link_lower
            for provider in (VideoProvider.Youtube, VideoProvider.YoutubeShort)
        )
        if is_youtube and "enablejsapi=1" not in link_lower:
            return add_query_param(self.video_link, "enablejsapi", "1")
        return self.video_link

    # Allow missing the first minute of the talk
    STREAMING_START_MARGIN: ClassVar[timedelta] = timedelta(minutes=1)

    @cached_property
    def streaming(self) -> Streaming | None:
        """
        Return the streaming covering this talk's slot, or ``None``.

        ``cached_property`` caches the result on the instance, so callers (the admin display,
        ``get_video_link``, ``get_transcription_url``, ``get_video_start_time``) all share one query
        per Talk. For list views, ``TalkQuerySet.with_streamings`` pre-populates the cache in a
        single batch query to avoid N+1.
        """
        if not self.room or not self.start_time:
            return None
        # At least half of the talk must be covered by the streaming
        min_duration = self.duration / 2
        return Streaming.objects.filter(
            room=self.room,
            start_time__lte=self.start_time + self.STREAMING_START_MARGIN,
            end_time__gte=self.start_time + min_duration,
        ).first()

    def get_streaming(self) -> Streaming | None:
        """Compatibility alias for ``self.streaming``; prefer the property in new code."""
        return self.streaming

    def get_video_start_time(self) -> int:
        """
        Return the video start time for this talk.

        Returns the talk's stored video_start_time if it exists.
        Otherwise, calculates the start time based on the corresponding streaming.
        Returns 0 if no start time can be determined.
        """
        if self.video_start_time is not None:
            return self.video_start_time

        # Calculate seconds between streaming start and talk start
        if streaming := self.streaming:
            return int((self.start_time - streaming.start_time).total_seconds())
        return 0

    def get_video_link(self) -> str:
        """
        Return the Video link for this talk.

        Returns the talk's own video_link if it exists.
        Otherwise, finds the appropriate streaming for this talk and returns its link with the
        correct timestamp.
        Returns an empty string if no link is found or if the talk is in the future.
        """
        # Optionally hide upcoming talks
        if self.get_timing() == self.TalkTiming.UPCOMING and not getattr(
            settings,
            "SHOW_UPCOMING_TALKS_LINKS",
            False,
        ):
            return ""

        if self.video_link:
            return self.video_link

        streaming = self.streaming
        if streaming:
            return streaming.video_link

        return ""

    @cached_property
    def video_provider(self) -> str:
        """
        Return the canonical video provider name for the talk's video link.

        Returns "Youtube" for both youtube.com and youtu.be links.
        Returns "Vimeo" for vimeo.com links.
        Returns an empty string when no video link is available.
        """
        video_link = self.get_video_link()
        for provider in VideoProvider:
            if provider in video_link:
                # Normalize the short YouTube URL variant to the same name as the
                # full one so templates only need to check for "Youtube".
                if provider == VideoProvider.YoutubeShort:
                    return VideoProvider.Youtube.name
                return provider.name
        return ""

    @cached_property
    def speaker_names(self) -> str:
        """
        Return a formatted list of speaker names.

        - 1 speaker: "Jane Smith"
        - 2 speakers: "Jane Smith & John Doe"
        - 3 speakers: "Jane Smith, John Doe & Julio Batista"
        - 4 or more: "Jane Smith, John Doe & 3 more"

        Cached after first access. Callers should use prefetch_related("speakers") on the queryset
        to avoid N+1 queries in list views.

        ``self.speakers.all()`` reuses the ``prefetch_related`` cache when present, but a
        ``.values_list("name")`` chained on it would issue a fresh query that ignores the
        cache, so iterate the prefetched objects directly.
        """
        speakers_list: list[str] = [s.name for s in self.speakers.all()]

        match speakers_list:
            case [single_name]:
                return single_name
            case [first, second]:
                return f"{first} & {second}"
            case [first, second, third]:
                return f"{first}, {second} & {third}"
            case [first, second, *others]:
                return f"{first}, {second} & {len(others)} more"
            case _:
                return ""

    @property
    def pretalx_code(self) -> str:
        """Return the Pretalx submission code parsed from ``pretalx_link``."""
        if not self.pretalx_link:
            return ""

        path = urlparse(self.pretalx_link).path.rstrip("/")
        if not path:
            return ""
        return path.rsplit("/", maxsplit=1)[-1]

    class TalkTiming(IntEnum):
        """
        Represents a talk's timing relative to now.

        PAST (-1): Talk has ended
        CURRENT (0): Talk is happening now (or very soon)
        UPCOMING (1): Talk will happen in the future
        """

        PAST = -1
        CURRENT = 0
        UPCOMING = 1

    def get_timing(self) -> TalkTiming:
        """Return if the talk is in the past, present or future."""
        now = timezone.now()
        margin = timedelta(minutes=5)
        end_time = self.start_time + self.duration

        if end_time + margin < now:
            return self.TalkTiming.PAST

        if self.start_time - margin > now:
            return self.TalkTiming.UPCOMING

        return self.TalkTiming.CURRENT

    def is_upcoming(self) -> bool:
        """
        Check if the talk is upcoming.

        Returns True if the talk is scheduled in the future, otherwise False.
        """
        return self.get_timing() == self.TalkTiming.UPCOMING

    def is_current(self) -> bool:
        """Return True if the talk is currently happening (within the timing margin)."""
        return self.get_timing() == self.TalkTiming.CURRENT

    def has_active_streaming(self) -> bool:
        """
        Check if the streaming associated with this talk is still ongoing.

        True: the video is in a live streaming.
        False: the talk was not streamed or the video is a recording.
        """
        streaming = self.streaming
        return bool(streaming and streaming.is_active())

    def get_image_url(self) -> str:
        """
        Return the image URL.

        Prefer the image field over the external image URL.
        Use a default placeholder image if neither is set.
        """
        if self.image:
            return self.image.url
        if self.external_image_url:
            return self.external_image_url
        subdir = self.event.slug if self.event else ""
        if subdir:
            return f"{settings.MEDIA_URL.rstrip('/')}/talk_images/{subdir}/default.jpg"
        return f"{settings.MEDIA_URL.rstrip('/')}/talk_images/default.jpg"

    def get_slido_link(self) -> str:
        """
        Return the Slido link for this talk.

        Returns the talk's own slido_link if it exists, otherwise falls back to the room's
        slido_link. Returns an empty string if neither exists.
        """
        if self.slido_link:
            return self.slido_link

        if self.room and self.room.slido_link:
            return self.room.slido_link

        return ""

    def get_transcription_url(self) -> str:
        """
        Return the transcription URL for this talk.

        Returns the talk's own transcription_url if set. Otherwise falls back to the
        transcription_url from the matched streaming session for this talk's room and time slot.
        Returns an empty string if neither source provides a URL.
        """
        if self.transcription_url:
            return self.transcription_url

        streaming = self.streaming
        if streaming and streaming.transcription_url:
            return streaming.transcription_url

        return ""

    def get_rating_stats(self) -> RatingStats:
        """
        Return the aggregate rating statistics for this talk in a single query.

        ``average`` is ``None`` when no ratings exist (matching Django's ``Avg`` semantics); callers
        that want a numeric fallback should coalesce explicitly.
        """
        stats = self.ratings.aggregate(average=Avg("score"), total=Count("id"))
        return RatingStats(average=stats["average"], total=stats["total"])


def _match_streaming(talk: Talk, candidates: list[Streaming]) -> Streaming | None:
    """Return the first streaming in ``candidates`` that covers ``talk``'s slot."""
    if not talk.start_time:
        return None
    window_start = talk.start_time + Talk.STREAMING_START_MARGIN
    min_end = talk.start_time + (talk.duration / 2)
    for s in candidates:
        if s.start_time <= window_start and s.end_time >= min_end:
            return s
    return None


def prefetch_streamings(talks: list[Talk]) -> None:
    """
    Batch-load streamings for the given talks and cache them per-instance.

    Each access to ``Talk.streaming`` triggers a query unless the value is already cached on the
    instance; in list views this would be an N+1 (one query per row from ``get_video_link`` /
    ``get_transcription_url`` / ``has_active_streaming``).  This helper runs a single ``IN`` query
    for every room involved, matches each talk to its covering streaming in Python, and writes the
    result into the ``streaming`` ``cached_property`` slot so subsequent access is free.

    Prefer ``TalkQuerySet.with_streamings`` in views; this lower-level function is used when you
    already have a list of talks (e.g. after pagination).

    Safe to call with an empty list; idempotent.
    """
    if not talks:
        return

    room_ids = {t.room_id for t in talks if t.room_id is not None}  # type: ignore[attr-defined]
    streamings_by_room: dict[int, list[Streaming]] = {}
    if room_ids:
        for s in Streaming.objects.filter(room_id__in=room_ids).only(
            "id",
            "room_id",
            "start_time",
            "end_time",
            "video_link",
            "transcription_url",
        ):
            streamings_by_room.setdefault(s.room_id, []).append(s)  # type: ignore[attr-defined]

    for t in talks:
        rid: int | None = t.room_id  # type: ignore[attr-defined]
        candidates = streamings_by_room.get(rid, []) if rid is not None else []
        # ``cached_property`` stores its value in the instance dict under the attribute
        # name, so writing here pre-populates the cache; later reads of ``t.streaming``
        # short-circuit without calling the property's loader.
        t.streaming = _match_streaming(t, candidates)


# Rating + SavedTalk models live in talks.models_rating. Import them here so that importing
# talks.models always registers every Talk-related model with Django - migrations, admin
# autodiscovery, and model_bakery all rely on that side effect.
from talks.models_pretalx import (  # noqa: E402
    MAX_PRETALX_CODE_LENGTH,
    PendingPretalxChange,
)
from talks.models_rating import (  # noqa: E402
    COMMENT_MAX_LENGTH,
    MAX_RATING_SCORE,
    MIN_RATING_SCORE,
    Rating,
    SavedTalk,
)


__all__ = [
    "COMMENT_MAX_LENGTH",
    "EMPTY_TRACK_NAME",
    "FAR_FUTURE",
    "LIGHTNING_TRACK_NAME",
    "MAX_PRETALX_CODE_LENGTH",
    "MAX_PRETALX_ID_LENGTH",
    "MAX_PRONOUNS_LENGTH",
    "MAX_RATING_SCORE",
    "MAX_ROOM_NAME_LENGTH",
    "MAX_SPEAKER_NAME_LENGTH",
    "MAX_TALK_TITLE_LENGTH",
    "MAX_TRACK_NAME_LENGTH",
    "MIN_RATING_SCORE",
    "PendingPretalxChange",
    "Rating",
    "RatingStats",
    "Room",
    "SavedTalk",
    "Speaker",
    "Streaming",
    "Talk",
    "TalkQuerySet",
]
