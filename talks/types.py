"""
Conference talk management module for PyCon DE & PyData 2025.

This module provides types that are used across the Talks model.
"""

from enum import StrEnum


"""Represents a streaming provider options"""


class VideoProvider(StrEnum):
    """Enumeration of video providers."""

    Youtube = "youtube.com"
    Vimeo = "vimeo.com"
