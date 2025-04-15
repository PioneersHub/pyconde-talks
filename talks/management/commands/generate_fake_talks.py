"""Management command to generate fake conference talks for testing."""
# ruff: noqa: C901 PLR0911 PLR0912 PLR0915 PLR2004 S311

import random
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from django.core.management.base import BaseCommand, CommandParser
from faker import Faker

from talks.models import Room, Speaker, Talk


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
            default="2025-04-23",
            help="Base conference date (YYYY-MM-DD)",
        )
        parser.add_argument(
            "--clear-existing",
            action="store_true",
            help="Clear existing rooms, talks and speakers before generating new ones",
        )

    def _create_rooms(self) -> dict[str, list[Room]]:
        """Create room objects for the conference."""
        # Darmstadium rooms
        rooms_plenary = ["Spectrum"]
        rooms_talks = [
            "Titanium",
            "Helium",
            "Platinum",
            "Europium",
            "Hassium",
            "Palladium",
        ]
        rooms_tutorials = ["Ferrum", "Dynamicum"]

        # Dictionary to store room objects by name
        room_objects = {}

        # Create plenary rooms
        for room_name in rooms_plenary:
            room, created = Room.objects.get_or_create(
                name=room_name,
                defaults={
                    "description": "Plenary room for keynotes and large events",
                    "capacity": random.randint(300, 500),
                    "slido_link": f"https://app.sli.do/event/{room_name.lower()}",
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
                    "slido_link": f"https://app.sli.do/event/{room_name.lower()}",
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
                    "slido_link": f"https://app.sli.do/event/{room_name.lower()}",
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

    def handle(self, *args: Any, **options: Any) -> None:  # noqa: ARG002
        """
        Generate fake conference talks.

        options:
            - count: Number of talks to generate
            - date: Base conference date
            - clear-existing: Whether to clear existing data

        """
        fake = Faker()
        talk_count = int(options["count"])

        # Clear existing data if requested
        if options.get("clear_existing"):
            self.stdout.write("Clearing existing data...")
            Talk.objects.all().delete()
            Speaker.objects.all().delete()
            Room.objects.all().delete()

        # Conference configuration
        base_time = datetime.strptime(str(options["date"]), "%Y-%m-%d").replace(
            hour=9,
            tzinfo=ZoneInfo("Europe/Berlin"),
        )

        # Create or get rooms
        self.stdout.write("Setting up conference rooms...")
        rooms = self._create_rooms()

        tracks = [
            "MLOps & DevOps",
            "Security",
            "Django & Web",
            "Natural Language Processing",
            "Machine Learning",
            "Data Handling & Engineering",
            "Computer Vision",
            "Programming & Software Engineering",
        ]

        # Create a pool of speakers with a number close to the talk count
        self.stdout.write("Generating pool of speakers...")
        speakers_pool = []
        for _ in range(int(talk_count * 0.9)):  # Create 90% as many speakers as talks
            # Select gender with a distribution
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

            # Generate name based on gender
            if gender == Speaker.Gender.MAN:
                name = fake.name_male()
            elif gender == Speaker.Gender.WOMAN:
                name = fake.name_female()
            else:
                name = fake.name()

            # Set gender self description if applicable
            gender_self_description = ""
            if gender == Speaker.Gender.SELF_DESCRIBE:
                gender_self_description = fake.word().capitalize()

            # Assign pronouns based on gender
            match gender:
                case Speaker.Gender.MAN:
                    pronouns = random.choice(["he/him", "he/they"])
                case Speaker.Gender.WOMAN:
                    pronouns = random.choice(["she/her", "she/they"])
                case Speaker.Gender.NON_BINARY | Speaker.Gender.GENDERQUEER:
                    pronouns = random.choice(["they/them", "ze/zir", "xe/xem"])
                case Speaker.Gender.SELF_DESCRIBE:
                    pronouns = random.choice(
                        [
                            "they/them",
                            "ze/zir",
                            "xe/xem",
                            "she/her",
                            "he/him",
                        ],
                    )
                case _:
                    pronouns = ""

            # Avatar URL generation based on gender
            avatar_url = ""
            if random.random() > 0.3:  # 70% of speakers have avatars
                if gender == Speaker.Gender.MAN:
                    avatar_url = (
                        f"https://randomuser.me/api/portraits/men/{random.randint(1, 99)}.jpg"
                    )
                elif gender == Speaker.Gender.WOMAN:
                    avatar_url = (
                        f"https://randomuser.me/api/portraits/women/{random.randint(1, 99)}.jpg"
                    )
                else:
                    # Lego avatars for others
                    avatar_url = (
                        f"https://randomuser.me/api/portraits/lego/{random.randint(1, 8)}.jpg"
                    )

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

        self.stdout.write(f"Generating {talk_count} talks...")

        # Generate talks
        for i in range(talk_count):
            talk_date = base_time + timedelta(
                days=random.randint(0, 2),
                hours=random.randint(0, 8),
                minutes=random.choice([0, 30]),
            )

            track = random.choice(tracks)
            title = self._generate_title(track, fake)

            # Determine presentation type
            presentation_type = random.choices(
                [
                    Talk.PresentationType.KEYNOTE,
                    Talk.PresentationType.KIDS,
                    Talk.PresentationType.PANEL,
                    Talk.PresentationType.TALK,
                    Talk.PresentationType.TUTORIAL,
                ],
                weights=[7, 3, 5, 70, 15],
            )[0]

            # Set room based on presentation type
            if presentation_type == Talk.PresentationType.KEYNOTE:
                room = random.choice(rooms["plenary"])
                duration = timedelta(minutes=45)
            elif presentation_type == Talk.PresentationType.TALK:
                room = random.choice(rooms["talks"])
                duration = timedelta(minutes=random.choice([30, 45]))
            else:  # Tutorial
                room = random.choice(rooms["tutorials"])
                duration = timedelta(minutes=random.choice([45, 90, 180]))

            # Decide whether to use room's Slido link or a custom one
            use_custom_slido = random.random() < 0.3  # 30% have custom Slido
            slido_link = ""
            if use_custom_slido:
                slido_link = (
                    f"https://app.sli.do/event/"
                    f"{fake.bothify(text='??????????????????????????')}"
                    f"/live/questions?m={fake.bothify(text='????#')}"
                )

            talk = Talk.objects.create(
                title=title,
                abstract=fake.paragraph(nb_sentences=3),
                description=fake.text(max_nb_chars=500),
                date_time=talk_date,
                duration=duration,
                room=room,
                track=track,
                presentation_type=presentation_type,
                pretalx_link=(
                    f"https://pretalx.com/pyconde-pydata-2025/talk/"
                    f"{fake.bothify(text='???###').upper()}"
                ),
                slido_link=slido_link,
                video_link=f"https://vimeo.com/{random.randint(100000000, 999999999)}",
                video_start_time=random.randint(0, 1800),
                hide=random.random() < 0.1,  # 10% of talks are hidden
            )

            # Add speakers to the talk (1 to 3 speakers)
            num_speakers = random.choices([1, 2, 3], weights=[70, 25, 5])[0]
            # Get random speakers without replacement
            selected_speakers = random.sample(speakers_pool, min(num_speakers, len(speakers_pool)))
            for speaker in selected_speakers:
                talk.speakers.add(speaker)

            self.stdout.write(
                self.style.SUCCESS(
                    f"Created {presentation_type} [{i + 1}/{talk_count}]: {talk.title} "
                    f"in {room.name} with {len(selected_speakers)} speaker(s)",
                ),
            )

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
