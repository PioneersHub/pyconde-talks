"""
Conference talk management module for PyCon DE & PyData 2025.

This module provides the Talk model for storing and managing conference talks, including their
metadata, scheduling information, and video links.
"""

from datetime import timedelta
from typing import Any, ClassVar

from django.conf import settings
from django.db import models
from django.utils import timezone


class Speaker(models.Model):
    """Represents a conference speaker."""

    name = models.CharField(
        max_length=200,
        help_text="Full name of the speaker",
    )

    biography = models.TextField(
        help_text="Biography of the speaker",
        blank=True,
    )

    avatar = models.URLField(
        help_text="URL to the speaker's avatar image",
        blank=True,
    )

    def __str__(self) -> str:
        """Return the speaker name."""
        return self.name


class Talk(models.Model):
    """Represents a conference talk."""

    class PresentationType(models.TextChoices):
        """Enumeration of presentation types."""

        TALK = "Talk", "Talk"
        TUTORIAL = "Tutorial", "Tutorial"

    presentation_type = models.CharField(
        max_length=10,
        choices=PresentationType.choices,
        default=PresentationType.TALK,
        help_text="Type of the presentation",
    )
    title = models.CharField(
        max_length=200,
        help_text="Title of the talk",
    )
    speakers = models.ManyToManyField(
        Speaker,
        related_name="talks",
        help_text="Speakers giving this talk",
    )
    abstract = models.TextField(
        help_text="Talk abstract",
    )
    description = models.TextField(
        help_text="Full description of the talk",
    )
    date_time = models.DateTimeField(
        help_text="Date and time when the talk is scheduled",
    )
    duration = models.DurationField(
        help_text="Duration of the talk",
        blank=True,
        null=True,
    )
    room = models.CharField(
        max_length=50,
        help_text="Room where the talk takes place",
    )
    track = models.CharField(
        max_length=50,
        help_text="Track or category of the talk",
    )
    external_image_url = models.URLField(
        help_text="URL to an externally hosted image",
        blank=True,
        default="",
    )
    image = models.ImageField(
        upload_to="talk_images/",
        help_text="Speaker or talk-related image (use if the external URL is bad or not available)",
        blank=True,
        null=True,
    )
    pretalx_link = models.URLField(
        help_text="Link to talk description in pretalx",
    )
    slido_link = models.URLField(
        help_text="Link to questions on Slido",
    )
    video_link = models.URLField(
        help_text="Link to talk recording on Vimeo",
        blank=True,
        default="",
    )
    video_start_time = models.PositiveIntegerField(
        help_text="Start time in seconds",
        blank=True,
        default=0,
    )
    hide = models.BooleanField(
        default=False,
        help_text="Hide this talk from the public",
    )
    created_at = models.DateTimeField(
        default=timezone.now,
        help_text="When this talk was added to the system",
    )
    updated_at = models.DateTimeField(
        auto_now=True,
        help_text="When this talk was last modified",
    )

    class Meta:
        """Metadata options for the Talk model."""

        ordering: ClassVar[list[str]] = ["date_time"]
        verbose_name = "Talk"
        verbose_name_plural = "Talks"
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
        """
        if self.duration is None:
            if self.presentation_type == self.PresentationType.TALK:
                self.duration = timedelta(minutes=30)
            elif self.presentation_type == self.PresentationType.TUTORIAL:
                self.duration = timedelta(minutes=45)
        super().save(*args, **kwargs)

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
            return self.image.url
        if self.external_image_url:
            return self.external_image_url
        return f"{settings.MEDIA_URL}talk_images/default.jpg"
