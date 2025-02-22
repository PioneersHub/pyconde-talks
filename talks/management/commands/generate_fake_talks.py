"""Management command to generate fake conference talks for testing."""
# ruff: noqa: S311

import random
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from django.core.management.base import BaseCommand, CommandParser
from faker import Faker

from talks.models import Talk


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

    def handle(self, **options: dict[str, str | int]) -> None:
        """
        Generate fake conference talks.

        Args:
            **options: Command options including:
                - count: Number of talks to generate
                - date: Base conference date

        """
        fake = Faker()
        talk_count = int(options["count"])

        # Conference configuration
        base_time = datetime.strptime(str(options["date"]), "%Y-%m-%d").replace(
            hour=9,
            tzinfo=ZoneInfo("Europe/Berlin"),
        )

        rooms = ["Main Hall", "Track 1", "Track 2", "Workshop Room", "Community Space"]
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

            talk = Talk.objects.create(
                title=title,
                speaker_name=fake.name(),
                description=fake.text(max_nb_chars=500),
                date_time=talk_date,
                room=random.choice(rooms),
                pretalx_link=(
                    f"https://pretalx.com/pyconde-pydata-2025/talk/"
                    f"{fake.bothify(text='???###').upper()}"
                ),
                video_link=f"https://vimeo.com/{random.randint(100000000, 999999999)}",
            )
            self.stdout.write(
                self.style.SUCCESS(f"Created talk [{i + 1}/{talk_count}]: {talk.title}"),
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
        if "ML" in track:
            return (
                f"Building {fake.company()} Scale {fake.bs()} "
                f"using {random.choice(['PyTorch', 'TensorFlow', 'scikit-learn'])}"
            )
        if "Security" in track:
            return f"Securing {fake.company_suffix()} Applications from {fake.bs()}"
        return f"{fake.catch_phrase()} with Python"
