"""Management command to generate fake conference talks for testing."""
# ruff: noqa: PLR0911 PLR2004 S311

import random
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any, cast

from django.conf import settings
from django.core.management.base import BaseCommand, CommandParser
from django.utils import timezone
from faker import Faker

from events.models import Event
from talks.models import Room, Speaker, Streaming, Talk


# Constants
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


@dataclass
class TalkGenerationContext:
    """Container for parameters needed to create a talk."""

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


class Command(BaseCommand):
    """Generate fake conference talks."""

    help = "Generate sample conference talks for testing purposes"

    def add_arguments(self, parser: CommandParser) -> None:
        """
        Add command line arguments.

        Args:
            parser: Command line argument parser for adding custom arguments

        """
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
            help="Clear existing rooms, talks, speakers, and streamings before generating new ones",
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
            "--event",
            type=str,
            default=getattr(settings, "DEFAULT_EVENT", ""),
            help="Event slug to associate generated talks with (default: DEFAULT_EVENT)",
        )

    # --------------------
    # Helper methods
    # --------------------
    def _select_pronouns(self, gender: Speaker.Gender) -> str:
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

    def _generate_avatar_url(self, gender: Speaker.Gender) -> str:
        """Generate an avatar URL for the speaker with a 70% chance of having one."""
        if random.random() <= 0.3:
            return ""
        if gender == Speaker.Gender.MAN:
            return f"https://randomuser.me/api/portraits/men/{random.randint(1, 99)}.jpg"
        if gender == Speaker.Gender.WOMAN:
            return f"https://randomuser.me/api/portraits/women/{random.randint(1, 99)}.jpg"
        return f"https://randomuser.me/api/portraits/lego/{random.randint(1, 8)}.jpg"

    def _build_pretalx_link(self, fake: Faker) -> str:
        """Construct a Pretalx talk link using the event's pretalx_url."""
        base_url = "https://pretalx.com/event"
        if self._event_obj:
            base_url = (self._event_obj.pretalx_url or base_url).rstrip("/")
        return f"{base_url}/talk/{fake.bothify(text='???###').upper()}"

    def _build_room_slido_link(self, room_name: str) -> str:
        """Return the default Slido link for a room name."""
        return f"https://app.sli.do/event/{room_name.lower()}"

    def _preload_streaming_by_room(self) -> dict[int, list[Streaming]]:
        """Preload and sort streaming sessions per room to avoid N+1 queries."""
        streaming_by_room: dict[int, list[Streaming]] = {}
        for session in Streaming.objects.select_related("room").all():
            streaming_by_room.setdefault(session.room.pk, []).append(session)
        for sessions in streaming_by_room.values():
            sessions.sort(key=lambda s: (s.start_time, s.end_time))
        return streaming_by_room

    def _create_speakers_pool(self, fake: Faker, talk_count: int) -> list[Speaker]:
        """Create a pool of speakers sized to ~90% of the talk_count."""
        speakers_pool: list[Speaker] = []
        for _ in range(int(talk_count * 0.9)):
            gender = random.choices(
                [
                    Speaker.Gender.MAN,
                    Speaker.Gender.WOMAN,
                    Speaker.Gender.NON_BINARY,
                    Speaker.Gender.GENDERQUEER,
                    Speaker.Gender.SELF_DESCRIBE,
                    Speaker.Gender.PREFER_NOT_TO_SAY,
                ],
                weights=[40, 40, 7, 5, 3, 5],
            )[0]

            if gender == Speaker.Gender.MAN:
                name = fake.name_male()
            elif gender == Speaker.Gender.WOMAN:
                name = fake.name_female()
            else:
                name = fake.name()

            gender_self_description = ""
            if gender == Speaker.Gender.SELF_DESCRIBE:
                gender_self_description = fake.word().capitalize()

            pronouns = self._select_pronouns(gender)
            avatar_url = self._generate_avatar_url(gender)

            speaker = Speaker.objects.create(
                name=name,
                biography=fake.text(max_nb_chars=300),
                avatar=avatar_url,
                gender=gender,
                gender_self_description=gender_self_description,
                pronouns=pronouns,
                pretalx_id=fake.bothify(text="???###").upper(),
            )
            speakers_pool.append(speaker)
            gender_label = dict(Speaker.Gender.choices).get(gender, "")
            self.stdout.write(
                f"Created speaker: {speaker.name} ({gender_label}, {speaker.pronouns})",
            )

        return speakers_pool

    def _build_special_slots(
        self,
        *,
        talk_count: int,
        now: datetime,
        streaming_now: Streaming,
    ) -> list[dict[str, Any]]:
        """Ensure we have finished, current, and near-future talks."""
        forced_streaming_room = streaming_now.room
        mid_stream_time = max(streaming_now.start_time, now)
        special_slots: list[dict[str, Any]] = [
            {"time": now - timedelta(hours=4)},
            {"time": mid_stream_time, "room": forced_streaming_room},
            {"time": now + timedelta(minutes=15)},
        ]
        return special_slots[: max(0, min(len(special_slots), talk_count))]

    def _choose_presentation_type(self) -> Talk.PresentationType:
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

    def _generate_title(self, track: str, fake: Faker) -> str:
        """
        Generate a realistic talk title based on the track.

        Args:
            track: The conference track name
            fake: Faker instance for generating fake data

        Returns:
            A generated talk title appropriate for the track

        """
        match track:
            case track if "ML" in track or "Machine Learning" in track:
                ml_framework = random.choice(["PyTorch", "TensorFlow", "scikit-learn"])
                return f"Building {fake.company()} Scale {fake.bs()} using {ml_framework}"
            case "Security":
                return f"Securing {fake.company_suffix()} Applications from {fake.bs()}"
            case track if "Django" in track:
                return f"Building {fake.catch_phrase()} with Django"
            case track if "Data" in track:
                data_tool = random.choice(["Pandas", "Polars", "PySpark"])
                return f"Data-driven {fake.bs()} with {data_tool}"
            case track if "Vision" in track:
                vision_tool = random.choice(["OpenCV", "YOLOv8", "TensorFlow"])
                return f"Detecting {fake.bs()} with {vision_tool}"
            case track if "NLP" in track or "Natural Language" in track:
                action = random.choice(["Building", "Training", "Fine-tuning"])
                model = random.choice(["GPT-4", "LLaMA", "Mistral"])
                return f"{action} {fake.catch_phrase()} with {model}"
            case track if "DevOps" in track:
                artifact = random.choice(["Pipeline", "Workflow", "Automation"])
                tool = random.choice(["Docker", "Kubernetes", "GitHub Actions"])
                return f"{fake.bs()} {artifact} with {tool}"
            case _:
                return f"{fake.catch_phrase()} with Python"

    def _create_talk(
        self,
        *,
        index: int,
        total: int,
        ctx: TalkGenerationContext,
        special_slot: dict[str, Any] | None,
    ) -> None:
        if special_slot is not None:
            talk_date = special_slot["time"]
            forced_room = special_slot.get("room")
        else:
            talk_date = ctx.base_time + timedelta(
                days=random.randint(0, 2),
                hours=random.randint(0, 8),
                minutes=random.choice([0, 30]),
            )
            forced_room = None

        track = random.choice(ctx.tracks)
        title = self._generate_title(track, ctx.fake)

        presentation_type = self._choose_presentation_type()
        room, duration = self._pick_room_and_duration(
            presentation_type=presentation_type,
            rooms=ctx.rooms,
            forced_room=forced_room,
        )

        streaming: Streaming | None = None
        if room:
            sessions = ctx.streaming_by_room.get(room.pk, [])
            for session in sessions:
                if session.start_time <= talk_date <= session.end_time:
                    streaming = session
                    break

        video_link = self._maybe_custom_video(
            has_streaming=bool(streaming),
            probability=float(ctx.talk_video_prob),
        )
        video_start_time = self._maybe_custom_video_start_time(
            duration,
            probability=float(ctx.video_start_prob),
        )
        slido_link = self._maybe_custom_slido(ctx.fake, probability=float(ctx.slido_prob))

        talk = Talk.objects.create(
            title=title,
            abstract=ctx.fake.paragraph(nb_sentences=3),
            description=ctx.fake.text(max_nb_chars=500),
            start_time=talk_date,
            duration=duration,
            room=room,
            track=track,
            presentation_type=presentation_type,
            pretalx_link=self._build_pretalx_link(ctx.fake),
            slido_link=slido_link,
            video_link=video_link,
            video_start_time=video_start_time,
            hide=random.random() < 0.1,
            event=ctx.event,
        )

        num_speakers = random.choices([1, 2, 3], weights=[70, 25, 5])[0]
        selected_speakers = random.sample(
            ctx.speakers_pool,
            min(num_speakers, len(ctx.speakers_pool)),
        )
        for speaker in selected_speakers:
            talk.speakers.add(speaker)

        streaming_info = " (with streaming)" if streaming else ""
        self.stdout.write(
            self.style.SUCCESS(
                (
                    f"Created {presentation_type} [{index}/{total}]: {talk.title} in {room.name}"
                    f"{streaming_info} with {len(selected_speakers)} speaker(s)"
                ),
            ),
        )

    def _pick_room_and_duration(
        self,
        *,
        presentation_type: Talk.PresentationType,
        rooms: dict[str, list[Room]],
        forced_room: Room | None,
    ) -> tuple[Room, timedelta]:
        """Pick a room and duration based on type or a forced room."""
        if forced_room is not None:
            return forced_room, timedelta(minutes=random.choice(TALK_SHORT_DURATIONS_MIN))
        if presentation_type == Talk.PresentationType.KEYNOTE:
            return random.choice(rooms["plenary"]), timedelta(minutes=KEYNOTE_DURATION_MIN)
        if presentation_type == Talk.PresentationType.TALK:
            return random.choice(rooms["talks"]), timedelta(
                minutes=random.choice(TALK_SHORT_DURATIONS_MIN),
            )
        # Tutorial, Kids, or Panel
        return random.choice(rooms["tutorials"]), timedelta(
            minutes=random.choice(TUTORIAL_DURATIONS_MIN),
        )

    def _maybe_custom_slido(self, fake: Faker, probability: float = 0.3) -> str:
        """Return a custom Slido link with the given probability (clamped to [0, 1])."""
        p = max(0.0, min(1.0, float(probability)))
        if random.random() >= p:
            return ""
        return (
            f"https://app.sli.do/event/"
            f"{fake.bothify(text='??????????????????????????')}"
            f"/live/questions?m={fake.bothify(text='????#')}"
        )

    def _maybe_custom_video(self, *, has_streaming: bool, probability: float = 0.3) -> str:
        """
        Return a Vimeo link based on probability when streaming exists.

        Probability is clamped to [0, 1].
        """
        if not has_streaming:
            return ""
        p = max(0.0, min(1.0, float(probability)))
        should_create = random.random() < p
        return f"https://vimeo.com/{random.randint(100000000, 999999999)}" if should_create else ""

    def _maybe_custom_video_start_time(self, duration: timedelta, probability: float = 0.1) -> int:
        """Return a random start offset with given probability, else 0."""
        p = max(0.0, min(1.0, float(probability)))
        if random.random() < p:
            return random.randint(0, int(duration.total_seconds() - 1))
        return 0

    def _create_rooms(
        self,
        *,
        rooms_plenary: list[str],
        rooms_talks: list[str],
        rooms_tutorials: list[str],
    ) -> dict[str, list[Room]]:
        """Create room objects for the conference."""
        # Room names provided via CLI defaults to Darmstadium set

        # Dictionary to store room objects by name
        room_objects = {}

        # Create plenary rooms
        for room_name in rooms_plenary:
            room, created = Room.objects.get_or_create(
                name=room_name,
                defaults={
                    "description": "Plenary room for keynotes and large events",
                    "capacity": random.randint(300, 500),
                    "slido_link": self._build_room_slido_link(room_name),
                },
            )
            if created:
                self.stdout.write(f"Created plenary room: {room_name}")
            room_objects[room_name] = room

        # Create talk rooms
        for room_name in rooms_talks:
            room, created = Room.objects.get_or_create(
                name=room_name,
                defaults={
                    "description": "Standard talk room",
                    "capacity": random.randint(100, 200),
                    "slido_link": self._build_room_slido_link(room_name),
                },
            )
            if created:
                self.stdout.write(f"Created talk room: {room_name}")
            room_objects[room_name] = room

        # Create tutorial rooms
        for room_name in rooms_tutorials:
            room, created = Room.objects.get_or_create(
                name=room_name,
                defaults={
                    "description": "Room for hands-on tutorials and workshops",
                    "capacity": random.randint(30, 80),
                    "slido_link": self._build_room_slido_link(room_name),
                },
            )
            if created:
                self.stdout.write(f"Created tutorial room: {room_name}")
            room_objects[room_name] = room

        return {
            "plenary": [room_objects[name] for name in rooms_plenary],
            "talks": [room_objects[name] for name in rooms_talks],
            "tutorials": [room_objects[name] for name in rooms_tutorials],
        }

    def _create_streaming_sessions(
        self,
        rooms: dict[str, list[Room]],
        base_time: datetime,
        *,
        days: int,
    ) -> None:
        """
        Create streaming sessions for each room.

        Args:
            rooms: Dictionary of room types to room objects
            base_time: Base start time for the conference
            days: Number of days to generate sessions for

        """
        self.stdout.write("Setting up streaming sessions...")

        # Clear existing streaming sessions if any
        Streaming.objects.all().delete()

        # Create streaming sessions for each day of the conference
        for day in range(int(days)):
            day_start = base_time + timedelta(days=day)

            # Always stream plenary rooms (for keynotes)
            for room in rooms["plenary"]:
                # Morning session
                morning_start = day_start.replace(hour=9, minute=0)
                morning_end = day_start.replace(hour=12, minute=30)

                # Afternoon session
                afternoon_start = day_start.replace(hour=13, minute=30)
                afternoon_end = day_start.replace(hour=18, minute=0)

                Streaming.objects.create(
                    room=room,
                    start_time=morning_start,
                    end_time=morning_end,
                    video_link=self._maybe_custom_video(has_streaming=True, probability=1),
                )

                Streaming.objects.create(
                    room=room,
                    start_time=afternoon_start,
                    end_time=afternoon_end,
                    video_link=self._maybe_custom_video(has_streaming=True, probability=1),
                )

                self.stdout.write(f"Created streaming sessions for {room.name} on day {day + 1}")

            # Stream most talk rooms but not all
            for room in rooms["talks"]:
                # 80% of talk rooms have streaming
                if random.random() < 0.8:
                    start_hour = random.choice([9, 10])
                    session_duration = random.randint(3, 5)  # hours

                    morning_start = day_start.replace(hour=start_hour, minute=0)
                    morning_end = morning_start + timedelta(hours=session_duration)

                    Streaming.objects.create(
                        room=room,
                        start_time=morning_start,
                        end_time=morning_end,
                        video_link=self._maybe_custom_video(has_streaming=True, probability=1),
                    )

                    # Some rooms may have afternoon sessions too
                    if random.random() < 0.6:
                        afternoon_start = day_start.replace(hour=14, minute=0)
                        afternoon_end = afternoon_start + timedelta(hours=random.randint(3, 4))

                        Streaming.objects.create(
                            room=room,
                            start_time=afternoon_start,
                            end_time=afternoon_end,
                            video_link=self._maybe_custom_video(has_streaming=True, probability=1),
                        )

                    self.stdout.write(
                        f"Created streaming sessions for {room.name} on day {day + 1}",
                    )

            # Tutorial rooms
            for room in rooms["tutorials"]:
                # 70% of tutorial rooms get streaming
                if random.random() < 0.7:
                    tutorial_start = day_start.replace(
                        hour=random.choice([9, 13]),
                        minute=0,
                    )
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

        # Ensure there is a session that covers now and at least the next 45 minutes
        now = timezone.now()
        min_end_time = now + timedelta(minutes=STREAMING_COVERAGE_MINUTES)
        has_covering_session = Streaming.objects.filter(
            start_time__lte=now,
            end_time__gte=min_end_time,
        ).exists()

        if not has_covering_session:
            # Create a 45-min session starting now in a random room
            target_room = random.choice(list(Room.objects.all()))
            Streaming.objects.create(
                room=target_room,
                start_time=now,
                end_time=now + timedelta(minutes=STREAMING_COVERAGE_MINUTES),
                video_link=self._maybe_custom_video(has_streaming=True, probability=1),
            )
            self.stdout.write(
                (
                    f"Created {STREAMING_COVERAGE_MINUTES}-min streaming session in "
                    f"{target_room.name} covering now"
                ),
            )

    def handle(self, *args: Any, **options: Any) -> None:  # noqa: ARG002
        """
        Generate fake conference talks.

        options:
            - count: Number of talks to generate
            - date: Base conference date
            - clear-existing: Whether to clear existing data

        """
        # Randomness
        fake = Faker()
        seed_value = options.get("seed")
        if seed_value is not None:
            random.seed(int(seed_value))
            fake.seed_instance(int(seed_value))
        talk_count = int(options["count"])

        # Clear existing data if requested
        if options.get("clear_existing"):
            self.stdout.write("Clearing existing data...")
            Talk.objects.all().delete()
            Speaker.objects.all().delete()
            Streaming.objects.all().delete()
            Room.objects.all().delete()

        # Base date: 09:00 of the first day
        try:
            base_date = date.fromisoformat(str(options["date"]))
        except ValueError as exc:
            message = "--date must be in YYYY-MM-DD format"
            raise ValueError(message) from exc
        base_time = timezone.make_aware(
            datetime.combine(base_date, time(9, 0)),
            timezone.get_current_timezone(),
        )

        # Parse rooms and tracks from CLI
        cli_rooms_plenary = [
            s.strip() for s in str(options["rooms_plenary"]).split(",") if s.strip()
        ]
        cli_rooms_talks = [s.strip() for s in str(options["rooms_talks"]).split(",") if s.strip()]
        cli_rooms_tutorials = [
            s.strip() for s in str(options["rooms_tutorials"]).split(",") if s.strip()
        ]

        # Create rooms
        self.stdout.write("Setting up conference rooms...")
        rooms = self._create_rooms(
            rooms_plenary=cli_rooms_plenary,
            rooms_talks=cli_rooms_talks,
            rooms_tutorials=cli_rooms_tutorials,
        )

        # Create streaming sessions for each room
        self._create_streaming_sessions(rooms, base_time, days=int(options["days"]))

        # Preload streaming sessions per room to reduce DB queries when generating talks
        streaming_by_room = self._preload_streaming_by_room()

        tracks = [s.strip() for s in str(options["tracks"]).split(",") if s.strip()] or TRACKS

        # Resolve or create Event
        event_obj: Event | None = None
        event_slug = str(options.get("event", "")).strip()
        if event_slug:
            event_obj, created = Event.objects.get_or_create(
                slug=event_slug,
                defaults={"name": event_slug, "year": 2025},
            )
            verb = "Created" if created else "Using existing"
            self.stdout.write(f"{verb} event '{event_obj.name}' (slug={event_obj.slug})")
        self._event_obj = event_obj

        # Create a pool of speakers with a number close to the talk count
        self.stdout.write("Generating pool of speakers...")
        speakers_pool = self._create_speakers_pool(fake=fake, talk_count=talk_count)

        self.stdout.write(f"Generating {talk_count} talks...")

        # Ensure presence of: finished, currently streaming, near future
        now = timezone.now()

        # Find a streaming session covering 'now' (guaranteed by _create_streaming_sessions)
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

        # Generate talks
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
        )

        for i in range(talk_count):
            special_slot = special_slots[i] if i < len(special_slots) else None
            self._create_talk(
                index=i + 1,
                total=talk_count,
                ctx=ctx,
                special_slot=special_slot,
            )
