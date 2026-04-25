"""
TalkGenerationContext: the mutable bundle of state threaded through talk creation.

Extracted from generate_fake_talks.py so the command file stays focused on the Command class.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from datetime import datetime

    from faker import Faker

    from events.models import Event
    from talks.models import Room, Speaker, Streaming

    from .availability import RoomAvailability


@dataclass
class TalkGenerationContext:
    """Container for shared state needed while generating talks."""

    fake: Faker
    base_time: datetime
    rooms: dict[str, list[Room]]
    tracks: list[str]
    streaming_by_room: dict[int, list[Streaming]]
    talk_video_prob: float
    video_start_prob: float
    slido_prob: float
    speakers_pool: list[Speaker]
    event: Event | None
    availability: RoomAvailability
