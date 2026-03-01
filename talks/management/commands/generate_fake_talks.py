"""Management command to generate fake conference talks for testing."""

# ruff: noqa: PLR0911, S311

import random
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any, NamedTuple, cast

from django.conf import settings
from django.core.management.base import BaseCommand, CommandParser
from django.utils import timezone
from faker import Faker

from events.models import Event
from talks.models import Room, Speaker, Streaming, Talk


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
STREAMING_COVERAGE_MINUTES = 45
KEYNOTE_DURATION_MIN = 45
TALK_SHORT_DURATIONS_MIN = [30, 45]
TUTORIAL_DURATIONS_MIN = [45, 90, 180]
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


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------
@dataclass
class SpecialSlot:
    """A time-pinned slot ensuring specific scheduling scenarios (past, live, near-future)."""

    time: datetime
    room: Room | None = None


class RoomAvailability:
    """
    Track free time intervals per room for conflict-free talk placement.

    Each room starts with one free interval per conference day (09:00 - 17:30).
    ``find_slot`` picks a random available start time aligned to 30-min
    boundaries, and ``reserve`` splits the containing interval so the time
    cannot be reused.
    """

    def __init__(
        self,
        rooms: dict[str, list[Room]],
        base_time: datetime,
        days: int,
    ) -> None:
        """Initialize with full-day free intervals for every room."""
        self._free: dict[int, list[tuple[datetime, datetime]]] = {}
        for room_list in rooms.values():
            for room in room_list:
                intervals: list[tuple[datetime, datetime]] = []
                for day in range(days):
                    day_start = base_time + timedelta(days=day)
                    day_end = day_start + timedelta(
                        hours=CONFERENCE_DAY_HOURS,
                        minutes=CONFERENCE_DAY_EXTRA_MINUTES,
                    )
                    intervals.append((day_start, day_end))
                self._free[room.pk] = intervals

    @staticmethod
    def _aligned_starts(
        iv_start: datetime,
        iv_end: datetime,
        duration: timedelta,
    ) -> list[datetime]:
        """Return 30-min-aligned start times that fit *duration* inside the interval."""
        remainder = iv_start.minute % SLOT_ALIGNMENT_MINUTES
        if remainder:
            first = iv_start + timedelta(minutes=SLOT_ALIGNMENT_MINUTES - remainder)
            first = first.replace(second=0, microsecond=0)
        else:
            first = iv_start.replace(second=0, microsecond=0)

        results: list[datetime] = []
        latest = iv_end - duration
        current = first
        while current <= latest:
            results.append(current)
            current += timedelta(minutes=SLOT_ALIGNMENT_MINUTES)
        return results

    def find_slot(
        self,
        rooms: list[Room],
        duration: timedelta,
    ) -> tuple[Room, datetime] | None:
        """Pick a random available ``(room, start_time)`` that fits *duration*, or ``None``."""
        candidates: list[tuple[Room, datetime]] = []
        for room in rooms:
            for iv_start, iv_end in self._free.get(room.pk, []):
                candidates.extend(
                    (room, start) for start in self._aligned_starts(iv_start, iv_end, duration)
                )
        if not candidates:
            return None
        return random.choice(candidates)

    def reserve(self, room: Room, start: datetime, duration: timedelta) -> None:
        """Mark ``[start, start + duration)`` as occupied by splitting free intervals."""
        end = start + duration
        new_intervals: list[tuple[datetime, datetime]] = []
        for iv_start, iv_end in self._free.get(room.pk, []):
            if iv_start < end and iv_end > start:
                if iv_start < start:
                    new_intervals.append((iv_start, start))
                if iv_end > end:
                    new_intervals.append((end, iv_end))
            else:
                new_intervals.append((iv_start, iv_end))
        self._free[room.pk] = new_intervals


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


class Command(BaseCommand):
    """Generate fake conference talks."""

    help = "Generate sample conference talks for testing purposes"

    def add_arguments(self, parser: CommandParser) -> None:
        """Add command-line arguments."""
        parser.add_argument(
            "--count",
            type=int,
            default=100,
            help="Number of talks to generate (default: 100)",
        )
        parser.add_argument(
            "--date",
            type=str,
            default=(timezone.localtime() - timedelta(days=1)).strftime("%Y-%m-%d"),
            help="Base conference date (YYYY-MM-DD)",
        )
        parser.add_argument(
            "--seed",
            type=int,
            default=None,
            help="Optional seed for deterministic data generation",
        )
        parser.add_argument(
            "--clear-existing",
            action="store_true",
            help="Clear existing rooms, talks, speakers, and streamings before generating",
        )
        parser.add_argument(
            "--talk-video-prob",
            type=float,
            default=0.3,
            help=(
                "Probability [0-1] a talk gets a custom video_link when streaming exists "
                "(default: 0.3)"
            ),
        )
        parser.add_argument(
            "--slido-prob",
            type=float,
            default=0.3,
            help="Probability [0-1] a talk gets a custom slido_link (default: 0.3)",
        )
        parser.add_argument(
            "--video-start-prob",
            type=float,
            default=0.1,
            help=(
                "Probability [0-1] a talk gets a custom video_start_time offset (seconds) "
                "(default: 0.1)"
            ),
        )
        parser.add_argument(
            "--days",
            type=int,
            default=3,
            help="Number of conference days to generate (default: 3)",
        )
        parser.add_argument(
            "--tracks",
            type=str,
            default=",".join(TRACKS),
            help="Comma-separated list of tracks (defaults to built-in tracks)",
        )
        parser.add_argument(
            "--rooms-plenary",
            type=str,
            default="Spectrum",
            help="Comma-separated list of plenary rooms (default: Spectrum)",
        )
        parser.add_argument(
            "--rooms-talks",
            type=str,
            default="Titanium,Helium,Platinum,Europium,Hassium,Palladium",
            help="Comma-separated list of talk rooms",
        )
        parser.add_argument(
            "--rooms-tutorials",
            type=str,
            default="Ferrum,Dynamicum",
            help="Comma-separated list of tutorial rooms",
        )
        parser.add_argument(
            "--event-slug",
            type=str,
            default=getattr(settings, "DEFAULT_EVENT", ""),
            help="Event slug to associate generated talks with (default: DEFAULT_EVENT)",
        )
        parser.add_argument(
            "--event-name",
            type=str,
            default="",
            help="Human-readable name for the event (used when creating a new Event).",
        )

    # ------------------------------------------------------------------
    # Speaker helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _select_pronouns(gender: Speaker.Gender) -> str:
        """Return pronouns string based on speaker gender."""
        match gender:
            case Speaker.Gender.MAN:
                return random.choice(["he/him", "he/they"])
            case Speaker.Gender.WOMAN:
                return random.choice(["she/her", "she/they"])
            case Speaker.Gender.NON_BINARY | Speaker.Gender.GENDERQUEER:
                return random.choice(["they/them", "ze/zir", "xe/xem"])
            case Speaker.Gender.SELF_DESCRIBE:
                return random.choice(["they/them", "ze/zir", "xe/xem", "she/her", "he/him"])
            case _:
                return ""

    @staticmethod
    def _generate_avatar_url(gender: Speaker.Gender) -> str:
        """Generate an avatar URL for the speaker (70% chance of having one)."""
        if random.random() >= AVATAR_PROBABILITY:
            return ""
        if gender == Speaker.Gender.MAN:
            return f"https://randomuser.me/api/portraits/men/{random.randint(1, 99)}.jpg"
        if gender == Speaker.Gender.WOMAN:
            return f"https://randomuser.me/api/portraits/women/{random.randint(1, 99)}.jpg"
        return f"https://randomuser.me/api/portraits/lego/{random.randint(1, 8)}.jpg"

    def _create_speaker(self, fake: Faker, gender: Speaker.Gender) -> Speaker:
        """Build a single ``Speaker`` instance (unsaved) for the given gender."""
        if gender == Speaker.Gender.MAN:
            name = fake.name_male()
        elif gender == Speaker.Gender.WOMAN:
            name = fake.name_female()
        else:
            name = fake.name()

        return Speaker(
            name=name,
            biography=fake.text(max_nb_chars=300),
            avatar=self._generate_avatar_url(gender),
            gender=gender,
            gender_self_description=(
                fake.word().capitalize() if gender == Speaker.Gender.SELF_DESCRIBE else ""
            ),
            pronouns=self._select_pronouns(gender),
            pretalx_id=fake.bothify(text="???###").upper(),
        )

    def _create_speakers_pool(self, fake: Faker, talk_count: int) -> list[Speaker]:
        """Create a pool of speakers sized to ~90% of *talk_count* via ``bulk_create``."""
        pool_size = int(talk_count * SPEAKER_POOL_RATIO)
        genders = random.choices(
            [
                Speaker.Gender.MAN,
                Speaker.Gender.WOMAN,
                Speaker.Gender.NON_BINARY,
                Speaker.Gender.GENDERQUEER,
                Speaker.Gender.SELF_DESCRIBE,
                Speaker.Gender.PREFER_NOT_TO_SAY,
            ],
            weights=[40, 40, 7, 5, 3, 5],
            k=pool_size,
        )
        speakers = [self._create_speaker(fake, g) for g in genders]
        speakers = Speaker.objects.bulk_create(speakers)
        self.stdout.write(f"Created {len(speakers)} speakers")
        return speakers

    @staticmethod
    def _assign_speakers(talk: Talk, speakers_pool: list[Speaker]) -> int:
        """Attach 1-3 random speakers from the pool to the talk. Return count assigned."""
        count = random.choices([1, 2, 3], weights=[70, 25, 5])[0]
        selected = random.sample(speakers_pool, min(count, len(speakers_pool)))
        for speaker in selected:
            talk.speakers.add(speaker)
        return len(selected)

    # ------------------------------------------------------------------
    # Link / URL helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _build_pretalx_link(fake: Faker, event: Event | None) -> str:
        """Construct a Pretalx talk link using the event's pretalx_url."""
        base_url = "https://pretalx.com/event"
        if event:
            base_url = (event.pretalx_url or base_url).rstrip("/")
        return f"{base_url}/talk/{fake.bothify(text='???###').upper()}"

    @staticmethod
    def _build_room_slido_link(room_name: str) -> str:
        """Return the default Slido link for a room name."""
        return f"https://app.sli.do/event/{room_name.lower()}"

    @staticmethod
    def _clamp_probability(value: float) -> float:
        """Clamp *value* to the ``[0.0, 1.0]`` range."""
        return max(0.0, min(1.0, float(value)))

    @staticmethod
    def _maybe_custom_slido(fake: Faker, probability: float = 0.3) -> str:
        """Return a custom Slido link with the given probability (clamped to [0, 1])."""
        if random.random() >= Command._clamp_probability(probability):
            return ""
        return (
            f"https://app.sli.do/event/"
            f"{fake.bothify(text='??????????????????????????')}"
            f"/live/questions?m={fake.bothify(text='????#')}"
        )

    @staticmethod
    def _maybe_custom_video(*, has_streaming: bool, probability: float = 0.3) -> str:
        """Return a Vimeo link based on probability when streaming exists."""
        if not has_streaming:
            return ""
        if random.random() >= Command._clamp_probability(probability):
            return ""
        return f"https://vimeo.com/{random.randint(100000000, 999999999)}"

    @staticmethod
    def _maybe_custom_video_start_time(duration: timedelta, probability: float = 0.1) -> int:
        """Return a random start offset with given probability, else 0."""
        if random.random() < Command._clamp_probability(probability):
            return random.randint(0, int(duration.total_seconds() - 1))
        return 0

    # ------------------------------------------------------------------
    # Streaming helpers
    # ------------------------------------------------------------------
    def _preload_streaming_by_room(self) -> dict[int, list[Streaming]]:
        """Preload and sort streaming sessions per room to avoid N+1 queries."""
        streaming_by_room: dict[int, list[Streaming]] = {}
        for session in Streaming.objects.select_related("room").all():
            streaming_by_room.setdefault(session.room.pk, []).append(session)
        for sessions in streaming_by_room.values():
            sessions.sort(key=lambda s: (s.start_time, s.end_time))
        return streaming_by_room

    @staticmethod
    def _find_streaming_session(
        streaming_by_room: dict[int, list[Streaming]],
        room: Room,
        talk_date: datetime,
    ) -> Streaming | None:
        """Return the streaming session covering *talk_date* in *room*, or ``None``."""
        for session in streaming_by_room.get(room.pk, []):
            if session.start_time <= talk_date <= session.end_time:
                return session
        return None

    def _create_plenary_sessions(self, room: Room, day_start: datetime, day: int) -> None:
        """Create morning and afternoon streaming sessions for a plenary room."""
        morning_start = day_start.replace(hour=9, minute=0)
        morning_end = day_start.replace(hour=12, minute=30)
        afternoon_start = day_start.replace(hour=13, minute=30)
        afternoon_end = day_start.replace(hour=18, minute=0)

        for start, end in [(morning_start, morning_end), (afternoon_start, afternoon_end)]:
            Streaming.objects.create(
                room=room,
                start_time=start,
                end_time=end,
                video_link=self._maybe_custom_video(has_streaming=True, probability=1),
            )
        self.stdout.write(f"Created streaming sessions for {room.name} on day {day + 1}")

    def _create_talk_room_sessions(self, room: Room, day_start: datetime, day: int) -> None:
        """Create streaming sessions for a talk room (80% chance, with optional afternoon)."""
        if random.random() >= TALK_ROOM_STREAMING_PROBABILITY:
            return

        start_hour = random.choice([9, 10])
        session_duration = random.randint(3, 5)
        morning_start = day_start.replace(hour=start_hour, minute=0)
        morning_end = morning_start + timedelta(hours=session_duration)

        Streaming.objects.create(
            room=room,
            start_time=morning_start,
            end_time=morning_end,
            video_link=self._maybe_custom_video(has_streaming=True, probability=1),
        )

        if random.random() < TALK_ROOM_AFTERNOON_PROBABILITY:
            afternoon_start = day_start.replace(hour=14, minute=0)
            afternoon_end = afternoon_start + timedelta(hours=random.randint(3, 4))
            Streaming.objects.create(
                room=room,
                start_time=afternoon_start,
                end_time=afternoon_end,
                video_link=self._maybe_custom_video(has_streaming=True, probability=1),
            )

        self.stdout.write(f"Created streaming sessions for {room.name} on day {day + 1}")

    def _create_tutorial_room_sessions(self, room: Room, day_start: datetime, day: int) -> None:
        """Create a streaming session for a tutorial room (70% chance)."""
        if random.random() >= TUTORIAL_ROOM_STREAMING_PROBABILITY:
            return

        tutorial_start = day_start.replace(hour=random.choice([9, 13]), minute=0)
        tutorial_end = tutorial_start + timedelta(hours=random.randint(3, 4))

        Streaming.objects.create(
            room=room,
            start_time=tutorial_start,
            end_time=tutorial_end,
            video_link=self._maybe_custom_video(has_streaming=True, probability=1),
        )
        self.stdout.write(
            f"Created streaming session for tutorial room {room.name} on day {day + 1}",
        )

    def _create_streaming_sessions(
        self,
        rooms: dict[str, list[Room]],
        base_time: datetime,
        *,
        days: int,
    ) -> None:
        """Create streaming sessions for each room, ensuring one covers the current time."""
        self.stdout.write("Setting up streaming sessions...")
        Streaming.objects.all().delete()

        for day in range(int(days)):
            day_start = base_time + timedelta(days=day)

            for room in rooms["plenary"]:
                self._create_plenary_sessions(room, day_start, day)

            for room in rooms["talks"]:
                self._create_talk_room_sessions(room, day_start, day)

            for room in rooms["tutorials"]:
                self._create_tutorial_room_sessions(room, day_start, day)

        self._ensure_current_streaming_coverage()

    def _ensure_current_streaming_coverage(self) -> None:
        """Guarantee at least one streaming session covers the current moment."""
        now = timezone.now()
        min_end_time = now + timedelta(minutes=STREAMING_COVERAGE_MINUTES)
        has_covering = Streaming.objects.filter(
            start_time__lte=now,
            end_time__gte=min_end_time,
        ).exists()

        if not has_covering:
            target_room = random.choice(list(Room.objects.all()))
            Streaming.objects.create(
                room=target_room,
                start_time=now,
                end_time=now + timedelta(minutes=STREAMING_COVERAGE_MINUTES),
                video_link=self._maybe_custom_video(has_streaming=True, probability=1),
            )
            self.stdout.write(
                f"Created {STREAMING_COVERAGE_MINUTES}-min streaming session in "
                f"{target_room.name} covering now",
            )

    # ------------------------------------------------------------------
    # Room helpers
    # ------------------------------------------------------------------
    def _create_rooms(
        self,
        rooms_by_category: dict[str, list[str]],
    ) -> dict[str, list[Room]]:
        """Create ``Room`` objects grouped by category (plenary, talks, tutorials)."""
        result: dict[str, list[Room]] = {}
        for category, names in rooms_by_category.items():
            cfg = _ROOM_CONFIGS[category]
            result[category] = []
            for name in names:
                room, created = Room.objects.get_or_create(
                    name=name,
                    defaults={
                        "description": cfg.description,
                        "capacity": random.randint(cfg.min_capacity, cfg.max_capacity),
                        "slido_link": self._build_room_slido_link(name),
                    },
                )
                if created:
                    self.stdout.write(f"Created {category} room: {name}")
                result[category].append(room)
        return result

    # ------------------------------------------------------------------
    # Talk scheduling
    # ------------------------------------------------------------------
    def _build_special_slots(
        self,
        *,
        talk_count: int,
        now: datetime,
        streaming_now: Streaming,
    ) -> list[SpecialSlot]:
        """Build time-pinned slots ensuring finished, current, and near-future talks."""
        forced_streaming_room = streaming_now.room
        mid_stream_time = max(streaming_now.start_time, now)
        special_slots = [
            SpecialSlot(time=now - timedelta(hours=4)),
            SpecialSlot(time=mid_stream_time, room=forced_streaming_room),
            SpecialSlot(time=now + timedelta(minutes=15)),
        ]
        return special_slots[: min(len(special_slots), talk_count)]

    @staticmethod
    def _choose_presentation_type() -> Talk.PresentationType:
        """Randomly choose a presentation type with configured weights."""
        return random.choices(
            [
                Talk.PresentationType.KEYNOTE,
                Talk.PresentationType.KIDS,
                Talk.PresentationType.LIGHTNING,
                Talk.PresentationType.PANEL,
                Talk.PresentationType.TALK,
                Talk.PresentationType.TUTORIAL,
            ],
            weights=[7, 2, 1, 5, 70, 15],
        )[0]

    @staticmethod
    def _get_room_candidates_and_duration(
        presentation_type: Talk.PresentationType,
        rooms: dict[str, list[Room]],
    ) -> tuple[list[Room], timedelta]:
        """Return candidate rooms and a random duration for *presentation_type*."""
        if presentation_type == Talk.PresentationType.KEYNOTE:
            return rooms["plenary"], timedelta(minutes=KEYNOTE_DURATION_MIN)
        if presentation_type == Talk.PresentationType.TALK:
            return rooms["talks"], timedelta(
                minutes=random.choice(TALK_SHORT_DURATIONS_MIN),
            )
        return rooms["tutorials"], timedelta(
            minutes=random.choice(TUTORIAL_DURATIONS_MIN),
        )

    @staticmethod
    def _pick_room_and_duration(
        *,
        presentation_type: Talk.PresentationType,
        rooms: dict[str, list[Room]],
        forced_room: Room | None,
    ) -> tuple[Room, timedelta]:
        """Pick a specific room and duration, optionally overriding with *forced_room*."""
        if forced_room is not None:
            return forced_room, timedelta(minutes=random.choice(TALK_SHORT_DURATIONS_MIN))
        candidates, duration = Command._get_room_candidates_and_duration(
            presentation_type,
            rooms,
        )
        return random.choice(candidates), duration

    def _resolve_slot(
        self,
        *,
        ctx: TalkGenerationContext,
        presentation_type: Talk.PresentationType,
        special_slot: SpecialSlot | None,
        index: int,
        total: int,
    ) -> tuple[Room, datetime, timedelta] | None:
        """Find a conflict-free ``(room, start_time, duration)`` or ``None``."""
        if special_slot is not None:
            return self._resolve_special_slot(
                ctx=ctx,
                presentation_type=presentation_type,
                special_slot=special_slot,
                index=index,
                total=total,
            )
        return self._resolve_normal_slot(
            ctx=ctx,
            presentation_type=presentation_type,
            index=index,
            total=total,
        )

    def _resolve_special_slot(
        self,
        *,
        ctx: TalkGenerationContext,
        presentation_type: Talk.PresentationType,
        special_slot: SpecialSlot,
        index: int,
        total: int,
    ) -> tuple[Room, datetime, timedelta] | None:
        """Resolve a time-pinned slot, falling back to alternative rooms on conflict."""
        talk_date = special_slot.time
        room, duration = self._pick_room_and_duration(
            presentation_type=presentation_type,
            rooms=ctx.rooms,
            forced_room=special_slot.room,
        )
        if Talk.has_room_conflict(room, talk_date, duration):
            candidates, _ = self._get_room_candidates_and_duration(
                presentation_type,
                ctx.rooms,
            )
            alt = next(
                (r for r in candidates if not Talk.has_room_conflict(r, talk_date, duration)),
                None,
            )
            if alt is None:
                self.stderr.write(
                    self.style.WARNING(
                        f"Skipped special talk [{index}/{total}]: "
                        f"no conflict-free room at {talk_date:%H:%M}",
                    ),
                )
                return None
            room = alt
        ctx.availability.reserve(room, talk_date, duration)
        return room, talk_date, duration

    def _resolve_normal_slot(
        self,
        *,
        ctx: TalkGenerationContext,
        presentation_type: Talk.PresentationType,
        index: int,
        total: int,
    ) -> tuple[Room, datetime, timedelta] | None:
        """Resolve a regular slot using ``RoomAvailability``."""
        candidates, duration = self._get_room_candidates_and_duration(
            presentation_type,
            ctx.rooms,
        )
        result = ctx.availability.find_slot(candidates, duration)
        if result is None:
            self.stderr.write(
                self.style.WARNING(
                    f"Skipped talk [{index}/{total}]: no available slot for "
                    f"{presentation_type} ({int(duration.total_seconds() // 60)} min)",
                ),
            )
            return None
        room, talk_date = result
        ctx.availability.reserve(room, talk_date, duration)
        return room, talk_date, duration

    # ------------------------------------------------------------------
    # Talk creation
    # ------------------------------------------------------------------
    @staticmethod
    def _generate_title(track: str, fake: Faker) -> str:
        """Generate a realistic talk title based on the track."""
        if "ML" in track or "Machine Learning" in track:
            framework = random.choice(["PyTorch", "TensorFlow", "scikit-learn"])
            return f"Building {fake.company()} Scale {fake.bs()} using {framework}"
        if track == "Security":
            return f"Securing {fake.company_suffix()} Applications from {fake.bs()}"
        if "Django" in track:
            return f"Building {fake.catch_phrase()} with Django"
        if "Data" in track:
            tool = random.choice(["Pandas", "Polars", "PySpark"])
            return f"Data-driven {fake.bs()} with {tool}"
        if "Vision" in track:
            tool = random.choice(["OpenCV", "YOLOv8", "TensorFlow"])
            return f"Detecting {fake.bs()} with {tool}"
        if "NLP" in track or "Natural Language" in track:
            action = random.choice(["Building", "Training", "Fine-tuning"])
            model = random.choice(["GPT-4", "LLaMA", "Mistral"])
            return f"{action} {fake.catch_phrase()} with {model}"
        if "DevOps" in track:
            artifact = random.choice(["Pipeline", "Workflow", "Automation"])
            tool = random.choice(["Docker", "Kubernetes", "GitHub Actions"])
            return f"{fake.bs()} {artifact} with {tool}"
        return f"{fake.catch_phrase()} with Python"

    def _create_talk(
        self,
        *,
        index: int,
        total: int,
        ctx: TalkGenerationContext,
        special_slot: SpecialSlot | None,
    ) -> None:
        """Create a single talk with speakers and optional streaming link."""
        presentation_type = self._choose_presentation_type()

        slot = self._resolve_slot(
            ctx=ctx,
            presentation_type=presentation_type,
            special_slot=special_slot,
            index=index,
            total=total,
        )
        if slot is None:
            return

        room, talk_date, duration = slot
        track = random.choice(ctx.tracks)
        streaming = self._find_streaming_session(ctx.streaming_by_room, room, talk_date)

        talk = Talk.objects.create(
            title=self._generate_title(track, ctx.fake),
            abstract=ctx.fake.paragraph(nb_sentences=3),
            description=ctx.fake.text(max_nb_chars=500),
            start_time=talk_date,
            duration=duration,
            room=room,
            track=track,
            presentation_type=presentation_type,
            pretalx_link=self._build_pretalx_link(ctx.fake, ctx.event),
            slido_link=self._maybe_custom_slido(ctx.fake, probability=float(ctx.slido_prob)),
            video_link=self._maybe_custom_video(
                has_streaming=bool(streaming),
                probability=float(ctx.talk_video_prob),
            ),
            video_start_time=self._maybe_custom_video_start_time(
                duration,
                probability=float(ctx.video_start_prob),
            ),
            hide=random.random() < HIDE_PROBABILITY,
            event=ctx.event,
        )

        speaker_count = self._assign_speakers(talk, ctx.speakers_pool)
        streaming_info = " (with streaming)" if streaming else ""
        self.stdout.write(
            self.style.SUCCESS(
                f"Created {presentation_type} [{index}/{total}]: {talk.title} in {room.name}"
                f"{streaming_info} with {speaker_count} speaker(s)",
            ),
        )

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------
    def handle(self, *args: Any, **options: Any) -> None:  # noqa: ARG002
        """Generate fake conference talks."""
        fake = Faker()
        seed_value = options.get("seed")
        if seed_value is not None:
            random.seed(int(seed_value))
            fake.seed_instance(int(seed_value))
        talk_count = int(options["count"])

        if options.get("clear_existing"):
            self.stdout.write("Clearing existing data...")
            Talk.objects.all().delete()
            Speaker.objects.all().delete()
            Streaming.objects.all().delete()
            Room.objects.all().delete()

        base_time = self._parse_base_time(str(options["date"]))

        self.stdout.write("Setting up conference rooms...")
        rooms = self._create_rooms(self._parse_room_names(options))

        self._create_streaming_sessions(rooms, base_time, days=int(options["days"]))
        streaming_by_room = self._preload_streaming_by_room()

        tracks = [s.strip() for s in str(options["tracks"]).split(",") if s.strip()] or TRACKS

        event_obj = self._resolve_event(options)

        self.stdout.write("Generating pool of speakers...")
        speakers_pool = self._create_speakers_pool(fake=fake, talk_count=talk_count)

        self.stdout.write(f"Generating {talk_count} talks...")

        now = timezone.now()
        streaming_now = cast(
            "Streaming",
            Streaming.objects.filter(
                start_time__lte=now,
                end_time__gte=now,
            ).first(),
        )
        special_slots = self._build_special_slots(
            talk_count=talk_count,
            now=now,
            streaming_now=streaming_now,
        )

        availability = RoomAvailability(rooms, base_time, days=int(options["days"]))
        ctx = TalkGenerationContext(
            fake=fake,
            base_time=base_time,
            rooms=rooms,
            tracks=tracks,
            streaming_by_room=streaming_by_room,
            talk_video_prob=float(options["talk_video_prob"]),
            video_start_prob=float(options["video_start_prob"]),
            slido_prob=float(options["slido_prob"]),
            speakers_pool=speakers_pool,
            event=event_obj,
            availability=availability,
        )

        for i in range(talk_count):
            special_slot = special_slots[i] if i < len(special_slots) else None
            self._create_talk(
                index=i + 1,
                total=talk_count,
                ctx=ctx,
                special_slot=special_slot,
            )

    @staticmethod
    def _parse_base_time(date_str: str) -> datetime:
        """Parse a YYYY-MM-DD string into a timezone-aware datetime at 09:00."""
        try:
            base_date = date.fromisoformat(date_str)
        except ValueError as exc:
            message = "--date must be in YYYY-MM-DD format"
            raise ValueError(message) from exc
        return timezone.make_aware(
            datetime.combine(base_date, time(9, 0)),
            timezone.get_current_timezone(),
        )

    @staticmethod
    def _parse_room_names(options: dict[str, Any]) -> dict[str, list[str]]:
        """Extract and split comma-separated room names from CLI options."""
        return {
            category: [s.strip() for s in str(options[cli_key]).split(",") if s.strip()]
            for category, cli_key in _ROOM_CLI_KEYS.items()
        }

    def _resolve_event(self, options: dict[str, Any]) -> Event | None:
        """Resolve or create the ``Event`` from CLI options."""
        event_slug = str(options.get("event_slug", "")).strip()
        event_name = str(options.get("event_name", "")).strip()
        if not event_slug:
            return None
        event_obj, created = Event.objects.get_or_create(
            slug=event_slug,
            defaults={"name": event_name or event_slug, "year": 2025},
        )
        verb = "Created" if created else "Using existing"
        self.stdout.write(f"{verb} event '{event_obj.name}' (slug={event_obj.slug})")
        return event_obj
