"""
Conference talk management module for PyCon DE & PyData 2025.

This module provides the Talk model for storing and managing conference talks, including their
metadata, scheduling information, and video links.
"""

from datetime import datetime, timedelta
from typing import Any, ClassVar, cast

from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _


# Constant for unscheduled talks
FAR_FUTURE = datetime(2050, 1, 1, 0, 0, 0, tzinfo=timezone.utc)


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
        max_length=200,
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
        help_text=_("Gender identity (optional)"),
        max_length=2,
        choices=Gender.choices,
        blank=True,
    )

    gender_self_description = models.CharField(
        max_length=100,
        blank=True,
        help_text=_("If you selected 'Self-describe', please specify your gender identity"),
    )

    pronouns = models.CharField(
        max_length=50,
        blank=True,
        help_text=_("Preferred pronouns (e.g., he/him, she/her, they/them)"),
    )

    def __str__(self) -> str:
        """Return the speaker name."""
        return self.name


class Talk(models.Model):
    """Represents a conference talk."""

    class PresentationType(models.TextChoices):
        """Enumeration of presentation types."""

        TALK = "Talk", _("Talk")
        TUTORIAL = "Tutorial", _("Tutorial")

    presentation_type = models.CharField(
        max_length=10,
        choices=PresentationType.choices,
        default=PresentationType.TALK,
        help_text=_("Type of the presentation"),
    )
    title = models.CharField(
        max_length=200,
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
        null=True,
        help_text=_("Duration of the talk"),
    )
    room = models.CharField(
        max_length=50,
        help_text=_("Room where the talk takes place"),
    )
    track = models.CharField(
        max_length=50,
        help_text=_("Track or category of the talk"),
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
    )
    slido_link = models.URLField(
        help_text=_("Link to questions on Slido"),
    )
    video_link = models.URLField(
        blank=True,
        default="",
        help_text=_("Link to talk recording on Vimeo"),
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
            return cast("str", self.image.url)
        if self.external_image_url:
            return self.external_image_url
        return f"{settings.MEDIA_URL}talk_images/default.jpg"
