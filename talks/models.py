"""
Conference talk management module for PyCon DE & PyData 2025.

This module provides the Talk model for storing and managing conference talks, including their
metadata, scheduling information, and video links.
"""

from datetime import UTC, datetime, timedelta
from typing import Any, ClassVar, cast

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _


# Constants
FAR_FUTURE = datetime(2050, 1, 1, 0, 0, 0, tzinfo=UTC)
MAX_PRETALX_ID_LENGTH = 50
MAX_PRONOUNS_LENGTH = 50
MAX_ROOM_NAME_LENGTH = 50
MAX_SPEAKER_NAME_LENGTH = 200
MAX_TALK_TITLE_LENGTH = 250
MAX_TRACK_NAME_LENGTH = 100


class Room(models.Model):
    """Represents a conference room where talks take place."""

    name = models.CharField(
        max_length=MAX_ROOM_NAME_LENGTH,
        unique=True,
        help_text=_("Name of the room"),
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

    class Meta:
        """Metadata for the Room model."""

        verbose_name = _("Room")
        verbose_name_plural = _("Rooms")
        ordering: ClassVar[list[str]] = ["name"]

    def __str__(self) -> str:
        """Return the room name."""
        return self.name


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
        help_text=_("Link to Vimeo streaming"),
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
        help_text=_("Unique identifier for the speaker in the Pretalx system"),
    )

    def __str__(self) -> str:
        """Return the speaker name."""
        return self.name


class Talk(models.Model):
    """Represents a conference talk."""

    class PresentationType(models.TextChoices):
        """
        Enumeration of presentation types.

        Values in Pretalx:
        - Keynote
        - Kids Workshop
        - Panel
        - Sponsored Talk
        - Sponsored Talk (Keystone)
        - Sponsored Talk (long)
        - Talk
        - Talk (long)
        - Tutorial
        """

        KEYNOTE = "Keynote", _("Keynote")
        KIDS = "Kids", _("Kids")
        PANEL = "Panel", _("Panel")
        TALK = "Talk", _("Talk")
        TUTORIAL = "Tutorial", _("Tutorial")

    DEFAULT_DURATIONS: ClassVar[dict] = {
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
    speakers = models.ManyToManyField(
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
    date_time = models.DateTimeField(
        blank=True,
        default=FAR_FUTURE,
        help_text=_("Date and time when the talk is scheduled"),
    )
    duration = models.DurationField(
        blank=True,
        default=timedelta(),
        help_text=_("Duration of the talk"),
    )
    room = models.ForeignKey(
        Room,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        help_text=_("Room where the talk takes place"),
        related_name="talks",
    )
    track = models.CharField(
        max_length=MAX_TRACK_NAME_LENGTH,
        help_text=_("Track or category of the talk"),
        blank=True,
        default="",
    )
    external_image_url = models.URLField(
        blank=True,
        default="",
        help_text=_("URL to an externally hosted image"),
    )
    image = models.ImageField(
        upload_to="talk_images/",
        blank=True,
        null=True,
        help_text=_("Image for the talk. Overrides the external image URL if provided."),
    )
    pretalx_link = models.URLField(
        help_text=_("Link to talk description in pretalx"),
        blank=True,
        default="",
    )
    slido_link = models.URLField(
        help_text=_("Link to questions on Slido. Overrides the room's link if provided."),
        blank=True,
        default="",
    )
    video_link = models.URLField(
        blank=True,
        default="",
        help_text=_(
            "Link to talk recording on Vimeo. Overrides the calculated streaming link if provided.",
        ),
    )
    video_start_time = models.PositiveIntegerField(
        blank=True,
        default=0,
        help_text=_("Start time in seconds"),
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

    class Meta:
        """Metadata options for the Talk model."""

        ordering: ClassVar[list[str]] = ["date_time"]
        verbose_name = _("Talk")
        verbose_name_plural = _("Talks")
        indexes: ClassVar[list[models.Index]] = [
            models.Index(fields=["date_time"]),
            models.Index(fields=["room"]),
        ]

    def __str__(self) -> str:
        """Return a string representation of the talk."""
        return f"{self.title} by {self.speaker_names}"

    def save(self, *args: Any, **kwargs: Any) -> None:
        """
        Save the talk instance.

        Set the duration based on the presentation type if not already set.
        Update video_start_time based on streaming if applicable.
        """
        if not self.duration:
            self.duration = self.DEFAULT_DURATIONS.get(self.presentation_type, timedelta())

        # Update video_start_time if necessary and possible
        if self.room and self.date_time and not self.video_link and not self.video_start_time:
            streaming = Streaming.objects.filter(
                room=self.room,
                start_time__lte=self.date_time,
                end_time__gte=self.date_time,
            ).first()

            if streaming:
                # Calculate seconds between streaming start and talk start
                self.video_start_time = int((self.date_time - streaming.start_time).total_seconds())

        super().save(*args, **kwargs)

    def get_video_link(self) -> str:
        """
        Return the Video link for this talk.

        Returns the talk's own video_link if it exists.
        Otherwise, finds the appropriate streaming for this talk and returns its link with the
        correct timestamp.
        Returns an empty string if no link is found or if the talk is in the future.
        """
        if self.is_upcoming():
            return ""

        if self.video_link:
            return self.video_link

        if self.room and self.date_time:
            # Find the streaming that is/was happening during the talk
            # Allow a 1 minute delay for the start time
            # At least half of the talk must be covered by the streaming
            margin = timedelta(minutes=1)
            min_duration = self.duration / 2

            streaming = Streaming.objects.filter(
                room=self.room,
                start_time__lte=self.date_time + margin,
                end_time__gt=self.date_time + min_duration,
            ).first()

            if streaming:
                return streaming.video_link

        return ""

    @property
    def speaker_names(self) -> str:
        """
        Return a formatted list of speaker names.

        - 1 speaker: "Jane Smith"
        - 2 speakers: "Jane Smith & John Doe"
        - 3 speakers: "Jane Smith, John Doe & Julio Batista"
        - 4 or more: "Jane Smith, John Doe & 3 more"
        """
        speakers_list = list(self.speakers.all().values_list("name", flat=True))

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

    def is_upcoming(self) -> bool:
        """Check if the talk is in the future."""
        return self.date_time > timezone.now()

    def get_image_url(self) -> str:
        """
        Return the image URL.

        Prefer the image field over the external image URL.
        Use a default placeholder image if neither is set.
        """
        if self.image:
            return cast("str", self.image.url)
        if self.external_image_url:
            return self.external_image_url
        return f"{settings.MEDIA_URL}talk_images/default.jpg"

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
