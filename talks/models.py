"""
Conference talk management module for PyCon DE & PyData 2025.

This module provides the Talk model for storing and managing conference talks, including their
metadata, scheduling information, and video links.
"""

from typing import ClassVar

from django.db import models
from django.utils import timezone


class Talk(models.Model):
    """Represents a conference talk."""

    title = models.CharField(
        max_length=200,
        help_text="Title of the talk",
    )
    speaker_name = models.CharField(
        max_length=100,
        help_text="Full name of the speaker",
    )
    description = models.TextField(
        help_text="Full description of the talk",
    )
    date_time = models.DateTimeField(
        help_text="Date and time when the talk is scheduled",
    )
    room = models.CharField(
        max_length=50,
        help_text="Room where the talk takes place",
    )
    image = models.ImageField(
        upload_to="talk_images/",
        help_text="Speaker or talk-related image",
        blank=True,
        null=True,
    )
    pretalx_link = models.URLField(
        help_text="Link to talk description in pretalx",
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
            models.Index(fields=["speaker_name"]),
        ]

    def __str__(self) -> str:
        """Return a string representation of the talk."""
        return f"{self.title} by {self.speaker_name}"

    def is_upcoming(self) -> bool:
        """Check if the talk is in the future."""
        return self.date_time > timezone.now()

    def get_duration(self) -> int | None:
        """Return talk duration in minutes if set."""
        return getattr(self, "duration", None)
