import os
import random
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import django
from faker import Faker


# Setup Django environment
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "pyconde_talks.settings")
django.setup()

from talks.models import Talk


def create_sample_talks():
    fake = Faker()

    # Conference dates
    base_time = datetime(2025, 4, 23, 9, 0, tzinfo=ZoneInfo("Europe/Berlin"))

    # Real conference rooms
    rooms = ["Main Hall", "Track 1", "Track 2", "Workshop Room", "Community Space"]

    # Real conference tracks for realistic talk titles
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

    # Generate 100 talks
    for i in range(100):
        # Randomize date and time
        talk_date = base_time + timedelta(
            days=random.randint(0, 2),
            hours=random.randint(0, 8),
            minutes=random.choice([0, 30]),
        )

        # Generate talk data
        track = random.choice(tracks)
        title = f"{fake.catch_phrase()} with Python"
        if "ML" in track:
            title = f"Building {fake.company()} Scale {fake.bs()} using {random.choice(['PyTorch', 'TensorFlow', 'scikit-learn'])}"
        elif "Security" in track:
            title = f"Securing {fake.company_suffix()} Applications from {fake.bs()}"

        talk = Talk.objects.create(
            title=title,
            speaker_name=fake.name(),
            description=fake.text(max_nb_chars=500),
            date_time=talk_date,
            room=random.choice(rooms),
            pretalx_link=f"https://pretalx.com/pyconde-2025/talk/{fake.bothify(text='???###').upper()}",
            video_link=f"https://vimeo.com/{random.randint(100000000, 999999999)}",
        )
        print(f"Created talk: {talk.title}")


if __name__ == "__main__":
    create_sample_talks()
