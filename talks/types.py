"""
Conference talk management module for the event talks site.

This module provides types that are used across the Talks model.
"""

from enum import StrEnum
from typing import NamedTuple


class VideoProvider(StrEnum):
    """Enumeration of video streaming providers."""

    Youtube = "youtube.com"
    YoutubeShort = "youtu.be"
    Vimeo = "vimeo.com"


class RatingStats(NamedTuple):
    """
    Aggregate rating statistics returned by ``Talk.get_rating_stats``.

    ``total`` is the number of ratings (kept distinct from the built-in tuple ``count`` method).
    Lives here rather than next to the Rating model so both ``talks.models`` and
    ``talks.models_rating`` can use it without importing from each other.
    """

    average: float | None
    total: int
