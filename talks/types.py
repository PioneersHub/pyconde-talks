"""
Conference talk management module for the event talks site.

This module provides types that are used across the Talks model.
"""

from enum import StrEnum


"""Represents a streaming provider options"""


class VideoProvider(StrEnum):
    """Enumeration of video providers."""

    Youtube = "youtube.com"
    YoutubeShort = "youtu.be"
    Vimeo = "vimeo.com"
