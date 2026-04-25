"""
Constants and RoomConfig shared by the generate_fake_talks command and its helpers.

Extracted from generate_fake_talks.py so the main file keeps its focus on the Command class.
"""

from typing import NamedTuple


# Streaming + talk length bounds (all in minutes)
STREAMING_COVERAGE_MINUTES = 45
KEYNOTE_DURATION_MIN = 45
TALK_SHORT_DURATIONS_MIN = [30, 45]
TUTORIAL_DURATIONS_MIN = [45, 90, 180]

# Tracks used to bucket generated talks and to drive some topic-specific title generation.
TRACKS = [
    "MLOps & DevOps",
    "Security",
    "Django & Web",
    "Natural Language Processing",
    "Machine Learning",
    "Data Handling & Engineering",
    "Computer Vision",
    "Programming & Software Engineering",
]

# Start times are rounded to this granularity so the schedule is grid-aligned.
SLOT_ALIGNMENT_MINUTES = 30

# Conference day length from the base start time.
CONFERENCE_DAY_HOURS = 8
CONFERENCE_DAY_EXTRA_MINUTES = 30

# Speaker and talk generation probabilities.
SPEAKER_POOL_RATIO = 0.9
AVATAR_PROBABILITY = 0.7
HIDE_PROBABILITY = 0.1
TALK_ROOM_STREAMING_PROBABILITY = 0.8
TALK_ROOM_AFTERNOON_PROBABILITY = 0.6
TUTORIAL_ROOM_STREAMING_PROBABILITY = 0.7


class RoomConfig(NamedTuple):
    """Description and capacity range for a room category."""

    description: str
    min_capacity: int
    max_capacity: int


_ROOM_CONFIGS: dict[str, RoomConfig] = {
    "plenary": RoomConfig("Plenary room for keynotes and large events", 300, 500),
    "talks": RoomConfig("Standard talk room", 100, 200),
    "tutorials": RoomConfig("Room for hands-on tutorials and workshops", 30, 80),
}

# Maps room category to the CLI option key used in ``add_arguments``.
_ROOM_CLI_KEYS: dict[str, str] = {
    "plenary": "rooms_plenary",
    "talks": "rooms_talks",
    "tutorials": "rooms_tutorials",
}
