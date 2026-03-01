"""
Conference talk management module for the event talks site.

This module provides types that are used across the Talks model.
"""

from enum import StrEnum


class VideoProvider(StrEnum):
    """Enumeration of video streaming providers."""

    Youtube = "youtube.com"
    YoutubeShort = "youtu.be"
    Vimeo = "vimeo.com"
