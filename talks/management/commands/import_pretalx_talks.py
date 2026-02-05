"""Management command for one-way (Pretalx » Django) sync of speakers and talks via API."""
# ruff: noqa: BLE001

import asyncio
import hashlib
import traceback
import warnings
from datetime import timedelta
from enum import Enum
from io import BytesIO
from pathlib import Path
from typing import Any, cast

import httpx
from django.conf import settings
from django.core.files.uploadedfile import InMemoryUploadedFile
from django.core.management.base import BaseCommand, CommandParser
from PIL import Image, ImageDraw, ImageFont, ImageOps, features
from pilmoji import Pilmoji
from pydantic import BaseModel, ValidationError
from pytanis import PretalxClient
from pytanis.config import PretalxCfg
from pytanis.pretalx.models import State, Submission, SubmissionSpeaker
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from talks.models import (
    FAR_FUTURE,
    MAX_ROOM_NAME_LENGTH,
    MAX_TALK_TITLE_LENGTH,
    MAX_TRACK_NAME_LENGTH,
    Room,
    Speaker,
    Talk,
)


class VerbosityLevel(Enum):
    """Enumeration for verbosity levels."""

    MINIMAL = 0
    NORMAL = 1
    DETAILED = 2
    DEBUG = 3
    TRACE = 4


class PytanisCfg(BaseModel):
    """Pytanis config for Pretalx only."""

    Pretalx: PretalxCfg


class SubmissionData:
    """Extract and hold clean data from a submission."""

    def __init__(
        self,
        submission: Submission,
        event_slug: str,
        pretalx_base_url: str | None = None,
    ) -> None:
        """Initialize the SubmissionData object."""
        self.submission = submission
        self.code = submission.code
        self.title = submission.title[:MAX_TALK_TITLE_LENGTH] if submission.title else ""
        self.abstract = submission.abstract or ""
        self.description = submission.description or ""
        base_url = cast(
            "str",
            pretalx_base_url or getattr(settings, "PRETALX_BASE_URL", "https://pretalx.com"),
        )
        base_url = base_url.rstrip("/")
        self.pretalx_link = f"{base_url}/{event_slug}/talk/{submission.code}"
        self.image_url = getattr(submission, "image", "") or ""

        # Extract room safely
        self.room = ""
        if (
            hasattr(submission, "slots")
            and submission.slots
            and hasattr(submission.slots[0], "room")
            and submission.slots[0].room
            and hasattr(submission.slots[0].room, "name")
            and submission.slots[0].room.name
            and "en" in submission.slots[0].room.name
        ):
            self.room = submission.slots[0].room.name["en"][:MAX_ROOM_NAME_LENGTH]

        # Extract track safely
        self.track = ""
        if (
            hasattr(submission, "track")
            and submission.track
            and hasattr(submission.track, "name")
            and submission.track.name
            and hasattr(submission.track.name, "en")
            and submission.track.name.en
        ):
            self.track = submission.track.name.en[:MAX_TRACK_NAME_LENGTH]

        # Extract start time safely
        self.start_time = FAR_FUTURE
        if (
            hasattr(submission, "slots")
            and submission.slots
            and hasattr(submission.slots[0], "start")
            and submission.slots[0].start
        ):
            self.start_time = submission.slots[0].start

        # Extract duration safely
        self.duration = None
        if hasattr(submission, "duration") and submission.duration:
            self.duration = timedelta(minutes=submission.duration)

        # Extract presentation type safely
        self.submission_type = ""
        if (
            hasattr(submission, "submission_type")
            and submission.submission_type
            and hasattr(submission.submission_type, "en")
            and submission.submission_type.en
        ):
            self.submission_type = str(submission.submission_type.en)


class Command(BaseCommand):
    """Fetch talks and speakers from Pretalx API and save them to the database."""

    help = "Import talks and speakers from Pretalx"

    def add_arguments(self, parser: CommandParser) -> None:
        """Add command line arguments."""
        parser.add_argument(
            "--pretalx-base-url",
            type=str,
            default=getattr(settings, "PRETALX_BASE_URL", "https://pretalx.com"),
            help="Base URL for Pretalx (used to build talk links)",
        )
        parser.add_argument(
            "--event",
            type=str,
            default=settings.PRETALX_EVENT_SLUG,
            help="Event slug for the Pretalx event",
        )
        parser.add_argument(
            "--api-token",
            type=str,
            default=settings.PRETALX_API_TOKEN,
            help="API token for the Pretalx API",
        )
        parser.add_argument(
            "--no-update",
            action="store_true",
            help="Skip updating existing talks and speakers",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Simulate the import without saving to the database",
        )
        parser.add_argument(
            "--max-retries",
            type=int,
            default=3,
            help="Maximum number of retries for API requests",
        )
        parser.add_argument(
            "--skip-images",
            action="store_true",
            help="Skip generating/updating talk social images",
        )
        parser.add_argument(
            "--image-format",
            type=str,
            choices=["webp", "jpeg"],
            default="webp",
            help="Output format for generated talk images (webp or jpeg). Default: webp",
        )

    def handle(self, *args: Any, **options: Any) -> None:  # noqa: ARG002
        """Execute the command to import talks from Pretalx."""
        # Extract options
        event_slug = options["event"]
        verbosity = VerbosityLevel(options["verbosity"])
        max_retries = options["max_retries"]

        try:
            # Setup and fetch data
            pretalx = self._setup_pretalx_client(options["api_token"], verbosity)

            self._log(
                f"Fetching talks from Pretalx event '{event_slug}'...",
                verbosity,
                VerbosityLevel.NORMAL,
            )

            # Fetch talks with retry logic
            try:
                talk_count, submissions = self._fetch_talks_with_retry(
                    pretalx,
                    event_slug,
                    max_retries,
                )
                self._log(
                    f"Fetched {talk_count} talks from Pretalx event '{event_slug}'",
                    verbosity,
                    VerbosityLevel.NORMAL,
                    style="SUCCESS",
                )
            except Exception as exc:
                self.stderr.write(self.style.ERROR(f"Failed to fetch talks: {exc!s}"))
                return

            # Process submissions
            self._process_submissions(list(submissions), event_slug, options)

        except Exception as exc:
            self.stderr.write(self.style.ERROR(f"An unexpected error occurred: {exc!s}"))
            if verbosity.value >= VerbosityLevel.DEBUG.value:
                self.stderr.write(traceback.format_exc())

    def _log(
        self,
        message: str,
        verbosity: VerbosityLevel,
        min_level: VerbosityLevel,
        style: str | None = None,
    ) -> None:
        """Log a message if verbosity level is sufficient."""
        if verbosity.value >= min_level.value:
            if style == "SUCCESS":
                self.stdout.write(self.style.SUCCESS(message))
            elif style == "WARNING":
                self.stdout.write(self.style.WARNING(message))
            elif style == "ERROR":
                self.stderr.write(self.style.ERROR(message))
            else:
                self.stdout.write(message)

    def _setup_pretalx_client(self, api_token: str, verbosity: VerbosityLevel) -> PretalxClient:
        """Set up and configure the Pretalx client."""
        config = PytanisCfg(Pretalx=PretalxCfg(api_token=api_token))
        client = PretalxClient(config)  # type: ignore[arg-type]

        # Configure throttling based on verbosity
        if verbosity.value >= VerbosityLevel.DETAILED.value:
            client.set_throttling(calls=5, seconds=1)

        return client

    def _fetch_talks_with_retry(
        self,
        pretalx: PretalxClient,
        event_slug: str,
        max_retries: int,
    ) -> tuple[int, Any]:
        """Fetch talks with retry logic."""

        @retry(
            stop=stop_after_attempt(max_retries),
            wait=wait_exponential(multiplier=1, min=1, max=60),
            retry=retry_if_exception_type(
                (httpx.HTTPStatusError, httpx.RequestError, RuntimeError, ValidationError),
            ),
        )
        def _retry_fetch_talks() -> tuple[int, list[Submission]]:
            if not settings.PICKLE_PRETALX_TALKS:
                count, submissions = pretalx.submissions(event_slug)
                return (count, list(submissions))

            import pickle  # nosec: B403  # noqa: PLC0415

            pickle_file = Path(".pretalx_cache")

            # Try cache first
            if pickle_file.exists():
                try:
                    with pickle_file.open("rb") as f:
                        return cast(
                            "tuple[int, list[Submission]]",
                            pickle.load(f),  # noqa: S301  # nosec: B301
                        )
                except (pickle.PickleError, OSError):  # fmt: skip
                    pass

            # Fetch and cache
            count, submissions = pretalx.submissions(event_slug)
            result = (count, list(submissions))

            try:
                with pickle_file.open("wb") as wb_file:
                    pickle.dump(result, wb_file)
            except OSError:
                pass

            return result

        return _retry_fetch_talks()

    def _process_submissions(
        self,
        submissions: list[Submission],
        event_slug: str,
        options: dict[str, Any],
    ) -> None:
        """Process a list of submissions."""
        # Extract options
        verbosity = VerbosityLevel(options["verbosity"])
        dry_run = options.get("dry_run", False)
        no_update = options.get("no_update", False)

        # Setup counters
        stats = {
            "total": len(submissions),
            "created": 0,
            "updated": 0,
            "deleted": 0,
            "skipped": 0,
            "failed": 0,
        }

        if dry_run:
            self._log(
                "DRY RUN: No changes will be saved to the database",
                verbosity,
                VerbosityLevel.NORMAL,
                "WARNING",
            )

        if no_update:
            self._log(
                "NO UPDATE: Existing talks and speakers will not be updated",
                verbosity,
                VerbosityLevel.NORMAL,
                "WARNING",
            )

        # Prefetch speaker avatar images to speed up talk image generation
        try:
            self._prefetch_avatars_for_submissions(submissions, options)
        except Exception as exc:  # Prefetch is best-effort
            self._log(
                f"Avatar prefetch failed (continuing without cache): {exc!s}",
                verbosity,
                VerbosityLevel.DETAILED,
                "WARNING",
            )

        # Batch create rooms and speakers before processing talks (optimization)
        if not dry_run:
            self._batch_create_rooms(submissions, event_slug, options)
            self._batch_create_or_update_speakers(submissions, options)

        # Process each submission
        for idx, submission in enumerate(submissions):
            self._log(
                f"Processing {idx + 1}/{stats['total']}: {submission.title}",
                verbosity,
                VerbosityLevel.DETAILED,
            )

            try:
                # Validate submission
                if not self._is_valid_submission(submission, verbosity):
                    stats["skipped"] += 1
                    continue

                # Process the submission
                result = self._process_single_submission(submission, event_slug, options)

                # Update stats
                stats[result] += 1

            except Exception as exc:
                stats["failed"] += 1
                self._log(
                    f"Error processing submission {submission.code}: {exc!s}",
                    verbosity,
                    VerbosityLevel.NORMAL,
                    "ERROR",
                )
                if verbosity.value >= VerbosityLevel.DEBUG.value:
                    self.stderr.write(traceback.format_exc())

        # Log completion statistics
        self._log(
            f"Import complete: {stats['created']} created, {stats['updated']} updated, "
            f"{stats['deleted']} deleted, {stats['skipped']} skipped, "
            f"{stats['failed']} failed, {stats['total']} total",
            verbosity,
            VerbosityLevel.NORMAL,
            "SUCCESS",
        )

    def _batch_create_rooms(
        self,
        submissions: list[Submission],
        event_slug: str,
        options: dict[str, Any],
    ) -> None:
        """Batch create all rooms needed for submissions to reduce database queries."""
        verbosity = VerbosityLevel(options.get("verbosity", VerbosityLevel.NORMAL.value))

        # Collect unique room names from submissions
        room_names: set[str] = set()
        for submission in submissions:
            # Only process accepted/confirmed submissions
            if submission.state not in [State.confirmed, State.accepted]:
                continue

            data = SubmissionData(submission, event_slug, options.get("pretalx_base_url"))
            if data.room:
                room_names.add(data.room)

        if not room_names:
            return

        # Get existing rooms
        existing_rooms = set(
            Room.objects.filter(name__in=room_names).values_list("name", flat=True),
        )

        # Create rooms that don't exist
        rooms_to_create = room_names - existing_rooms
        if rooms_to_create:
            Room.objects.bulk_create(
                [
                    Room(name=name, description=f"Room imported from Pretalx: {name}")
                    for name in rooms_to_create
                ],
                ignore_conflicts=True,
            )
            self._log(
                f"Batch created {len(rooms_to_create)} rooms",
                verbosity,
                VerbosityLevel.DETAILED,
                "SUCCESS",
            )

    def _collect_speakers_from_submissions(
        self,
        submissions: list[Submission],
    ) -> dict[str, SubmissionSpeaker]:
        """Collect unique speakers from accepted/confirmed submissions."""
        speakers_data: dict[str, SubmissionSpeaker] = {}
        valid_states = {State.confirmed, State.accepted}
        for submission in submissions:
            if submission.state not in valid_states or not submission.speakers:
                continue
            for speaker in submission.speakers:
                speakers_data[speaker.code] = speaker
        return speakers_data

    def _batch_create_or_update_speakers(
        self,
        submissions: list[Submission],
        options: dict[str, Any],
    ) -> None:
        """Batch create or update all speakers to reduce database queries."""
        verbosity = VerbosityLevel(options.get("verbosity", VerbosityLevel.NORMAL.value))
        no_update = options.get("no_update", False)

        # Collect unique speakers from submissions
        speakers_data = self._collect_speakers_from_submissions(submissions)
        if not speakers_data:
            return

        # Get existing speakers by pretalx_id
        existing_speakers = {
            s.pretalx_id: s for s in Speaker.objects.filter(pretalx_id__in=speakers_data.keys())
        }

        # Separate new speakers from existing ones
        speakers_to_create: list[Speaker] = []
        speakers_to_update: list[Speaker] = []

        for code, speaker_data in speakers_data.items():
            if code not in existing_speakers:
                speakers_to_create.append(
                    Speaker(
                        name=speaker_data.name,
                        biography=speaker_data.biography or "",
                        avatar=speaker_data.avatar_url or "",
                        pretalx_id=speaker_data.code,
                    ),
                )
            elif not no_update:
                existing = existing_speakers[code]
                bio = speaker_data.biography or ""
                avatar = speaker_data.avatar_url or ""
                if (
                    existing.name != speaker_data.name
                    or existing.biography != bio
                    or existing.avatar != avatar
                ):
                    existing.name = speaker_data.name
                    existing.biography = bio
                    existing.avatar = avatar
                    speakers_to_update.append(existing)

        # Bulk create new speakers
        if speakers_to_create:
            Speaker.objects.bulk_create(speakers_to_create, ignore_conflicts=True)
            self._log(
                f"Batch created {len(speakers_to_create)} speakers",
                verbosity,
                VerbosityLevel.DETAILED,
                "SUCCESS",
            )

        # Bulk update existing speakers
        if speakers_to_update:
            Speaker.objects.bulk_update(
                speakers_to_update,
                ["name", "biography", "avatar"],
            )
            self._log(
                f"Batch updated {len(speakers_to_update)} speakers",
                verbosity,
                VerbosityLevel.DETAILED,
                "SUCCESS",
            )

    def _prefetch_avatars_for_submissions(
        self,
        submissions: list[Submission],
        options: dict[str, Any],
    ) -> None:
        """Collect unique avatar URLs (accepted/confirmed only) and prefetch them."""
        # Don't prefetch if images are skipped
        if options.get("skip_images", False):
            return
        urls: set[str] = set()
        for sub in submissions:
            if getattr(sub, "state", None) not in {State.accepted, State.confirmed}:
                continue
            if getattr(sub, "speakers", None):
                for sp in sub.speakers:
                    url = getattr(sp, "avatar_url", None) or ""
                    if url:
                        urls.add(url)

        if not urls:
            return
        cache_dir = self._get_avatar_cache_dir()
        asyncio.run(_prefetch_avatar_urls(urls, cache_dir))

    def _get_avatar_cache_dir(self) -> Path:
        """Return the on-disk avatar cache directory path."""
        return Path(settings.MEDIA_ROOT) / "avatars"

    def _is_valid_submission(self, submission: Submission, verbosity: VerbosityLevel) -> bool:
        """Validate a submission."""
        valid = True

        if not submission.title:
            valid = False
            self._log(
                f"Submission {submission.code} has no title",
                verbosity,
                VerbosityLevel.NORMAL,
                "ERROR",
            )

        if submission.title and len(submission.title) > MAX_TALK_TITLE_LENGTH:
            self._log(
                f"Submission title too long, will be truncated: {submission.title}",
                verbosity,
                VerbosityLevel.NORMAL,
                "WARNING",
            )

        if not submission.speakers:
            if self._submission_is_lightning_talk(submission) or self._submission_is_announcement(
                submission,
            ):
                # Always allow Lightning Talks and announcements without defined speakers
                valid = True
                self._log(
                    f"Submission {submission.code} has no speakers",
                    verbosity,
                    VerbosityLevel.NORMAL,
                    "WARNING",
                )
            else:
                # Maybe allow other submissions without defined speakers
                valid = settings.IMPORT_TALKS_WITHOUT_SPEAKERS
                self._log(
                    f"Submission {submission.code} has no speakers",
                    verbosity,
                    VerbosityLevel.NORMAL,
                    "WARNING",
                )

        # Rooms are mandatory
        if (
            not hasattr(submission, "slots")
            or not submission.slots
            or not hasattr(submission.slots[0], "room")
            or not submission.slots[0].room
        ):
            self._log(
                f"Submission {submission.code} has no room assigned",
                verbosity,
                VerbosityLevel.TRACE,
                "WARNING",
            )

        return valid

    def _submission_is_lightning_talk(self, submission: Submission) -> bool:
        """Check if a submission is a lightning talk."""
        lightning_terms = frozenset(
            {
                "lightning",
                "lightning talk",
                "lightning talks",
                "lightning talks (1/2)",
                "lightning talks (2/2)",
            },
        )
        fields = [
            getattr(submission, "track", None),
            getattr(submission, "title", None),
            getattr(submission, "submission_type", None),
        ]
        for field in fields:
            # If field is a string
            if isinstance(field, str) and field.lower() in lightning_terms:
                return True
            # If field is a MultiLingualStr, check .en
            if (
                field is not None
                and hasattr(field, "en")
                and isinstance(field.en, str)
                and field.en.lower() in lightning_terms
            ):
                return True
        return False

    def _submission_is_announcement(self, submission: Submission) -> bool:
        """Check if a submission is an announcement (like opening and closing sessions)."""
        announcement_terms = frozenset(
            {
                "opening session",
                "closing session",
            },
        )
        fields = [
            getattr(submission, "track", None),
            getattr(submission, "title", None),
            getattr(submission, "submission_type", None),
        ]
        for field in fields:
            # If field is a string
            if isinstance(field, str) and field.lower() in announcement_terms:
                return True
            # If field is a MultiLingualStr, check .en
            if (
                field is not None
                and hasattr(field, "en")
                and isinstance(field.en, str)
                and field.en.lower() in announcement_terms
            ):
                return True
        return False

    def _process_single_submission(
        self,
        submission: Submission,
        event_slug: str,
        options: dict[str, Any],
    ) -> str:
        """Process a single submission and return the result status."""
        # Extract options
        verbosity = VerbosityLevel(options["verbosity"])
        dry_run = options.get("dry_run", False)
        no_update = options.get("no_update", False)

        # Extract structured data from submission
        data = SubmissionData(submission, event_slug, options.get("pretalx_base_url"))

        # Check if talk exists
        existing_talk = Talk.objects.filter(pretalx_link=data.pretalx_link).first()

        # Delete canceled talk
        if submission.state not in [State.confirmed, State.accepted]:
            if existing_talk:
                self._log(
                    f"Talk {data.title} changed state to {submission.state}. Deleting",
                    verbosity,
                    VerbosityLevel.NORMAL,
                    "WARNING",
                )

                if not dry_run:
                    # Delete the talk
                    existing_talk.delete()

                return "deleted"
            return "skipped"

        if existing_talk:
            if no_update:
                self._log(
                    f"Skipping update for existing talk: {data.title}",
                    verbosity,
                    VerbosityLevel.DETAILED,
                    "WARNING",
                )
                return "skipped"

            self._log(
                f"Updating existing talk: {data.title}",
                verbosity,
                VerbosityLevel.DETAILED,
                "WARNING",
            )

            if not dry_run:
                self._update_talk(existing_talk, data, submission.speakers, options)
            return "updated"
        self._log(f"Creating new talk: {data.title}", verbosity, VerbosityLevel.DETAILED)

        if not dry_run:
            talk = self._create_talk(data, options)
            self._add_speakers_to_talk(talk, submission.speakers, options)
            if not options.get("skip_images", False):
                self._generate_talk_image(talk, options)
        return "created"

    def _create_talk(self, data: SubmissionData, options: dict[str, Any]) -> Talk:
        """Create a new Talk from submission data."""
        verbosity = VerbosityLevel(options.get("verbosity", VerbosityLevel.NORMAL.value))

        # Map presentation type
        presentation_type = self._map_presentation_type(data.submission_type, data.code, verbosity)

        # Get or create room
        room = None
        if data.room:
            room = self._get_or_create_room(data.room, options)

        # Create the talk
        talk = Talk.objects.create(
            presentation_type=presentation_type,
            title=data.title,
            abstract=data.abstract,
            description=data.description,
            start_time=data.start_time,
            duration=data.duration,
            room=room,
            track=data.track,
            pretalx_link=data.pretalx_link,
            external_image_url=data.image_url,
        )

        self._log(f"Created talk: {data.title}", verbosity, VerbosityLevel.DETAILED, "SUCCESS")
        return talk

    def _update_talk(
        self,
        talk: Talk,
        data: SubmissionData,
        speakers: list[SubmissionSpeaker],
        options: dict[str, Any],
    ) -> None:
        """Update an existing Talk with data from a Submission."""
        verbosity = VerbosityLevel(options.get("verbosity", VerbosityLevel.NORMAL.value))
        no_update = options.get("no_update", False)

        if no_update:
            self._log(
                f"Skipping update due to --no-update flag: {talk.title}",
                verbosity,
                VerbosityLevel.DETAILED,
                "WARNING",
            )
            return

        # Update fields
        talk.title = data.title
        talk.abstract = data.abstract
        talk.description = data.description
        talk.start_time = data.start_time

        if data.duration:
            talk.duration = data.duration

        # Get or create room
        if data.room:
            talk.room = self._get_or_create_room(data.room, options)
        else:
            talk.room = None

        talk.track = data.track

        if data.image_url:
            talk.external_image_url = data.image_url

        # Update presentation type
        talk.presentation_type = self._map_presentation_type(
            data.submission_type,
            data.code,
            verbosity,
        )

        talk.save()

        self._log(f"Updated talk: {talk.title}", verbosity, VerbosityLevel.DETAILED, "SUCCESS")

        # Update speakers
        self._update_talk_speakers(talk, speakers, options)

    def _add_speakers_to_talk(
        self,
        talk: Talk,
        speakers: list[SubmissionSpeaker],
        options: dict[str, Any],
    ) -> None:
        """Add speakers to a talk."""
        verbosity = VerbosityLevel(options.get("verbosity", VerbosityLevel.NORMAL.value))

        for speaker_data in speakers:
            speaker = self._get_or_create_speaker(speaker_data, options)
            talk.speakers.add(speaker)

        if speakers and verbosity.value >= VerbosityLevel.DETAILED.value:
            self._log(
                f"Added {len(speakers)} speakers to talk: {talk.title}",
                verbosity,
                VerbosityLevel.DETAILED,
                "SUCCESS",
            )

    def _update_talk_speakers(
        self,
        talk: Talk,
        submission_speakers: list[SubmissionSpeaker],
        options: dict[str, Any],
    ) -> None:
        """Update the speakers for a talk, adding new ones and removing old ones."""
        verbosity = VerbosityLevel(options.get("verbosity", VerbosityLevel.NORMAL.value))
        dry_run = options.get("dry_run", False)
        no_update = options.get("no_update", False)

        if dry_run:
            self._log(
                f"Would update speakers for talk: {talk.title} (dry run)",
                verbosity,
                VerbosityLevel.DETAILED,
            )
            return

        if no_update:
            self._log(
                f"Skipping speaker updates due to --no-update flag: {talk.title}",
                verbosity,
                VerbosityLevel.DETAILED,
                "WARNING",
            )
            return

        # Get current speaker pretalx_ids
        current_speaker_ids = set(talk.speakers.all().values_list("pretalx_id", flat=True))

        # Get new speaker pretalx_ids
        new_speaker_ids = {speaker.code for speaker in submission_speakers}

        # Add new speakers
        speakers_to_add = []
        for speaker_data in submission_speakers:
            if speaker_data.code not in current_speaker_ids:
                speaker = self._get_or_create_speaker(speaker_data, options)
                speakers_to_add.append(speaker)

        if speakers_to_add:
            talk.speakers.add(*speakers_to_add)
            self._log(
                f"Added {len(speakers_to_add)} speakers to talk: {talk.title}",
                verbosity,
                VerbosityLevel.DETAILED,
                "SUCCESS",
            )

        # Remove speakers no longer associated
        speakers_to_remove = current_speaker_ids - new_speaker_ids
        if speakers_to_remove:
            speakers_to_remove_objs = talk.speakers.filter(pretalx_id__in=speakers_to_remove)
            removed_count = speakers_to_remove_objs.count()
            talk.speakers.remove(*speakers_to_remove_objs)
            self._log(
                f"Removed {removed_count} speakers from talk: {talk.title}",
                verbosity,
                VerbosityLevel.DETAILED,
                "SUCCESS",
            )

    def _get_or_create_room(
        self,
        room_name: str,
        options: dict[str, Any],
    ) -> Room | None:
        """Get an existing room or create a new one."""
        # If no room name provided, return None
        if not room_name:
            return None

        verbosity = VerbosityLevel(options.get("verbosity", VerbosityLevel.NORMAL.value))
        dry_run = options.get("dry_run", False)

        # Check if room exists by name
        existing_room = Room.objects.filter(name=room_name).first()

        if existing_room:
            # Room already exists
            self._log(
                f"Using existing room: {room_name}",
                verbosity,
                VerbosityLevel.DETAILED,
            )
            return existing_room

        # Create new room
        if dry_run:
            self._log(
                f"Would create room: {room_name} (dry run)",
                verbosity,
                VerbosityLevel.DETAILED,
                "SUCCESS",
            )
            # Return a dummy room for dry run
            return Room(
                name=room_name,
                description="",
            )

        room = Room.objects.create(
            name=room_name,
            description=f"Room imported from Pretalx: {room_name}",
        )
        self._log(
            f"Created room: {room_name}",
            verbosity,
            VerbosityLevel.DETAILED,
            "SUCCESS",
        )
        return room

    def _get_or_create_speaker(
        self,
        speaker_data: SubmissionSpeaker,
        options: dict[str, Any],
    ) -> Speaker:
        """Get an existing speaker or create a new one."""
        verbosity = VerbosityLevel(options.get("verbosity", VerbosityLevel.NORMAL.value))
        no_update = options.get("no_update", False)
        dry_run = options.get("dry_run", False)

        # Check if speaker exists by pretalx_id
        existing_speaker = Speaker.objects.filter(pretalx_id=speaker_data.code).first()

        if existing_speaker:
            # Update speaker info if needed and not in no-update mode
            if (
                not no_update
                and not dry_run
                and (
                    existing_speaker.biography != (speaker_data.biography or "")
                    or existing_speaker.avatar != (speaker_data.avatar_url or "")
                    or existing_speaker.name != speaker_data.name
                )
            ):
                existing_speaker.name = speaker_data.name
                existing_speaker.biography = speaker_data.biography or ""
                existing_speaker.avatar = speaker_data.avatar_url or ""
                existing_speaker.save()
                self._log(
                    f"Updated speaker: {speaker_data.name}",
                    verbosity,
                    VerbosityLevel.DETAILED,
                    "SUCCESS",
                )
            elif no_update and verbosity.value >= VerbosityLevel.DETAILED.value:
                self._log(
                    f"Skipping update for existing speaker: {speaker_data.name} (--no-update)",
                    verbosity,
                    VerbosityLevel.DETAILED,
                    "WARNING",
                )
            return existing_speaker

        # Create new speaker
        if dry_run:
            self._log(
                f"Would create speaker: {speaker_data.name} (dry run)",
                verbosity,
                VerbosityLevel.DETAILED,
                "SUCCESS",
            )
            # Return a dummy speaker for dry run
            return Speaker(
                name=speaker_data.name,
                biography=speaker_data.biography or "",
                avatar=speaker_data.avatar_url or "",
                pretalx_id=speaker_data.code,
            )

        speaker = Speaker.objects.create(
            name=speaker_data.name,
            biography=speaker_data.biography or "",
            avatar=speaker_data.avatar_url or "",
            pretalx_id=speaker_data.code,
        )
        self._log(
            f"Created speaker: {speaker_data.name}",
            verbosity,
            VerbosityLevel.DETAILED,
            "SUCCESS",
        )
        return speaker

    def _map_presentation_type(
        self,
        submission_type: str | None,
        submission_code: str,
        verbosity: VerbosityLevel,
    ) -> str:
        """Map Pretalx submission type to Django model presentation type."""
        # Handle None or empty string
        if not submission_type:
            self._log(
                f"Empty presentation type for submission {submission_code}, defaulting to 'Talk'",
                verbosity,
                VerbosityLevel.DETAILED,
                "WARNING",
            )
            return Talk.PresentationType.TALK

        type_mapping = {
            "Keynote": Talk.PresentationType.KEYNOTE,
            "Kids Workshop": Talk.PresentationType.KIDS,
            "Lightning Talks": Talk.PresentationType.LIGHTNING,
            "Panel": Talk.PresentationType.PANEL,
            "Plenary Session [Organizers]": Talk.PresentationType.PLENARY,
            "Sponsored Talk (Keystone)": Talk.PresentationType.TUTORIAL,
            "Sponsored Talk (long)": Talk.PresentationType.TALK,
            "Sponsored Talk": Talk.PresentationType.TALK,
            "Talk (long) [Sponsored]": Talk.PresentationType.TALK,
            "Talk (long)": Talk.PresentationType.TALK,
            "Talk [Sponsored]": Talk.PresentationType.TALK,
            "Talk": Talk.PresentationType.TALK,
            "Tutorial [Sponsored]": Talk.PresentationType.TUTORIAL,
            "Tutorial": Talk.PresentationType.TUTORIAL,
        }

        # Check if the type is recognized
        if submission_type not in type_mapping:
            self._log(
                f"Unrecognized presentation type '{submission_type}' for submission "
                f"{submission_code}, defaulting to 'Talk'",
                verbosity,
                VerbosityLevel.DETAILED,
                "WARNING",
            )
            return Talk.PresentationType.TALK

        return type_mapping[submission_type]

    def _download_speaker_photo(self, speaker: Speaker) -> Image.Image | None:
        """Get the speaker photo from cache or download it if needed."""
        url = speaker.avatar
        if not url:
            return None

        cache_dir = self._get_avatar_cache_dir()
        data = _get_cached_avatar_bytes(cache_dir, url)

        # Download and persist if missing
        if data is None:
            data = _download_avatar_bytes_sync(url)
            if data is None:
                return None
            _save_avatar_bytes(cache_dir, url, data)

        try:
            return Image.open(BytesIO(data))
        except Exception:
            return None

    def _process_speaker_photo(self, photo: Image.Image, size: int = 200) -> Image.Image:
        """Crop to square, resize, and apply a circular alpha mask."""
        # Crop to centered square and resize in one go
        img = ImageOps.fit(photo, (size, size), Image.Resampling.LANCZOS, centering=(0.5, 0.5))

        # Build circular alpha mask
        mask = Image.new("L", (size, size), 0)
        ImageDraw.Draw(mask).ellipse((0, 0, size, size), fill=255)

        # Compose RGBA
        output = Image.new("RGBA", (size, size))
        output.paste(img.convert("RGB"), (0, 0))
        output.putalpha(mask)
        return output

    def _download_and_process_speaker_photos(
        self,
        speakers: list[Speaker],
        limit: int = 2,
    ) -> list[tuple[Image.Image, str]]:
        """Download and process speaker photos."""
        photos = []
        for speaker in speakers:
            photo = self._download_speaker_photo(speaker)
            if photo:
                photo = self._process_speaker_photo(photo)
                photos.append((photo, speaker.pretalx_id))
                if len(photos) >= limit:
                    break
        return photos

    def _wrap_text(
        self,
        text: str,
        fonts: dict[str, ImageFont.FreeTypeFont],
        max_width: int,
    ) -> list[str]:
        """Greedy wrap within max_width using Pilmoji-based measurement for accuracy."""
        words = text.split()
        lines: list[str] = []
        current: list[str] = []

        for word in words:
            trial = " ".join([*current, word])
            width = self._pilmoji_text_width(trial, fonts["title"], max_width)

            if width <= max_width:
                current.append(word)
            else:
                if current:
                    lines.append(" ".join(current))
                current = [word]

        if current:
            lines.append(" ".join(current))

        return lines

    def _pilmoji_text_width(
        self,
        text: str,
        font: ImageFont.FreeTypeFont,
        max_width: int,
    ) -> int:
        """
        Measure rendered width by drawing with Pilmoji on a temporary image.

        We render into a transparent canvas and inspect the bounding box, which accounts for emoji
        images and shaped text.
        """
        # Heuristic canvas size: twice the max width and ~2x font size height
        canvas_w = max(64, max_width * 2)
        canvas_h = max(64, int(font.size * 2))
        img = Image.new("RGBA", (canvas_w, canvas_h), (0, 0, 0, 0))
        with Pilmoji(img) as pilmoji:
            pilmoji.text((0, 0), text, (255, 255, 255), font)
        bbox = img.getbbox()
        return 0 if bbox is None else bbox[2]

    def _load_fonts(self) -> dict[str, ImageFont.FreeTypeFont]:
        """
        Load the configured font for the social card.

        Configuration:
        - Django setting TALK_CARD_FONT must point to an existing .ttf/.otf file.
        """
        font_path = getattr(settings, "TALK_CARD_FONT", None)
        if not font_path or not Path(font_path).exists():
            msg = "TALK_CARD_FONT must be configured and point to an existing font file"
            raise FileNotFoundError(msg)

        layout = ImageFont.Layout.RAQM if features.check_feature("raqm") else ImageFont.Layout.BASIC
        fonts: dict[str, ImageFont.FreeTypeFont] = {
            "title": ImageFont.truetype(font_path, 46, layout_engine=layout),
            "subtitle": ImageFont.truetype(font_path, 28, layout_engine=layout),
            "small": ImageFont.truetype(font_path, 24, layout_engine=layout),
            "event_info": ImageFont.truetype(font_path, 42, layout_engine=layout),
        }
        return fonts

    def _generate_talk_image(  # noqa: PLR0915
        self,
        talk: Talk,
        options: dict[str, Any],
        card_width: int = 1920,
    ) -> Image.Image:
        """Generate a talk image based on the title and speakers and save as WebP."""
        verbosity = VerbosityLevel(options.get("verbosity", VerbosityLevel.NORMAL.value))
        # Determine output format (default to webp)
        image_format_raw = cast("str | None", options.get("image_format", "webp")) or "webp"
        image_format = image_format_raw.lower()
        if image_format == "jpg":  # allow common alias
            image_format = "jpeg"
        if image_format not in {"webp", "jpeg"}:
            self._log(
                f"Unsupported image format '{image_format_raw}', defaulting to 'webp'",
                verbosity,
                VerbosityLevel.DETAILED,
                "WARNING",
            )
            image_format = "webp"

        template_path = (
            settings.BASE_DIR
            / "assets"
            / "img"
            / settings.BRAND_ASSETS_SUBDIR
            / "talk_template.png"
        )
        img = Image.open(template_path).copy().convert("RGBA")

        # Get template dimensions
        width, height = img.size

        # Convert to RGB for final output
        final_img = Image.new("RGB", (width, height), (255, 255, 255))
        final_img.paste(img, (0, 0), img)

        draw = ImageDraw.Draw(final_img)

        # Use exact positions
        speaker_margin_x = 40
        speaker_margin_y = 50

        # Download up to 4 speaker photos
        limit = 4
        speaker_photos = self._download_and_process_speaker_photos(
            list(talk.speakers.all()),
            limit=limit,
        )
        avatar_count = len(speaker_photos)

        # Decide layout and sizes
        spacing = 20
        area_side = int(height * 0.5)  # square area for speakers at top-left

        if avatar_count <= 1:
            speaker_size = area_side
            grid_cols = 1
        elif avatar_count == 2:  # noqa: PLR2004
            area_width = int(height * 0.7)
            speaker_size = (area_width - spacing) // 2
            grid_cols = 2
        else:
            speaker_size = (area_side - spacing) // 2
            grid_cols = 2

        # Process photos to the computed size
        processed_photos = [
            self._process_speaker_photo(p, size=speaker_size) for p, _ in speaker_photos[:limit]
        ]

        # Paste photos in grid at upper-left
        for idx, photo in enumerate(processed_photos):
            row = idx // grid_cols
            col = idx % grid_cols
            x = speaker_margin_x + col * (speaker_size + spacing)
            y = speaker_margin_y + row * (speaker_size + spacing)
            final_img.paste(photo, (x, y), photo)

        # Session title: align at bottom of safe zone box
        full_width = card_width - 80

        # Fonts and colors
        fonts = self._load_fonts()
        colors = {"text": (255, 255, 255)}

        self._draw_title_block(
            canvas=final_img,
            title=talk.title,
            fonts=fonts,
            full_width=full_width,
        )

        # Speaker names at bottom
        speakers_text = talk.speaker_names
        if speakers_text:
            speaker_y = height - 80
            draw.text(
                (60, speaker_y),
                speakers_text,
                font=fonts["subtitle"],
                fill=colors["text"],
            )

        # Save WebP
        buffer = BytesIO()
        if image_format == "webp":
            final_img.save(buffer, format="WEBP", quality=82, method=6)
            content_type = "image/webp"
            ext = "webp"
        else:  # JPEG
            # JPEG doesn't support alpha; final_img is RGB already
            final_img.save(
                buffer,
                format="JPEG",
                quality=88,
                optimize=True,
                progressive=True,
            )
            content_type = "image/jpeg"
            ext = "jpeg"
        buffer.seek(0)
        image_file = InMemoryUploadedFile(
            buffer,
            None,
            f"talk_{talk.pk}.{ext}",
            content_type,
            buffer.getbuffer().nbytes,
            None,
        )
        talk.image = image_file
        talk.save()

        self._log(
            f"Generated talk image for: {talk.title}",
            verbosity,
            VerbosityLevel.DETAILED,
            "SUCCESS",
        )

        return final_img

    def _draw_title_block(
        self,
        canvas: Image.Image,
        title: str,
        fonts: dict[str, ImageFont.FreeTypeFont],
        full_width: int,
    ) -> None:
        """Draw wrapped title text aligned to bottom of safe area."""
        title_lines = self._wrap_text(title, fonts, full_width)
        line_height = 80
        title_block_height = len(title_lines[:5]) * line_height
        title_y = 900 - title_block_height
        with Pilmoji(canvas) as pilmoji:
            for line in title_lines[:5]:
                pilmoji.text((60, title_y), line, (255, 255, 255), fonts["title"])
                title_y += line_height


# In-memory avatar cache
AVATAR_CACHE: dict[str, bytes] = {}


def _url_to_cache_path(cache_dir: Path, url: str) -> Path:
    """Return on-disk cache path for a URL using SHA-256 hash."""
    h = hashlib.sha256(url.encode("utf-8")).hexdigest()
    return cache_dir / f"{h}.img"


def _get_cached_avatar_bytes(cache_dir: Path, url: str) -> bytes | None:
    """Return cached bytes from memory or disk (and hydrate memory if from disk)."""
    data = AVATAR_CACHE.get(url)
    if data is not None:
        return data
    path = _url_to_cache_path(cache_dir, url)
    if path.exists():
        try:
            data = path.read_bytes()
        except Exception as exc:
            warnings.warn(f"Failed to read avatar from disk cache: {exc!s}", stacklevel=2)
        else:
            AVATAR_CACHE[url] = data
            return data
    return None


def _save_avatar_bytes(cache_dir: Path, url: str, data: bytes) -> None:
    """Persist avatar bytes to disk and memory cache."""
    path = _url_to_cache_path(cache_dir, url)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    except Exception as exc:
        warnings.warn(f"Avatar cache write failed: {exc!s}", stacklevel=2)
    AVATAR_CACHE[url] = data


def _download_avatar_bytes_sync(url: str, request_timeout: float = 15) -> bytes | None:
    """Download avatar bytes synchronously; return None on failure."""
    try:
        resp = httpx.get(url, timeout=request_timeout)
        resp.raise_for_status()
    except httpx.HTTPError:
        return None
    else:
        return resp.content


async def _download_avatar_bytes_async(
    client: httpx.AsyncClient,
    url: str,
    request_timeout: float = 15,
) -> bytes | None:
    """Download avatar bytes asynchronously; return None on failure."""
    try:
        resp = await client.get(url, timeout=request_timeout)
        resp.raise_for_status()
    except httpx.HTTPError:
        return None
    else:
        return resp.content


async def _prefetch_avatar_urls(urls: set[str], cache_dir: Path, concurrency: int = 8) -> None:
    """Prefetch avatar URLs into memory and disk cache using httpx.AsyncClient."""

    async def _fetch(client: httpx.AsyncClient, url: str) -> None:
        # Skip if already cached (memory or disk)
        if _get_cached_avatar_bytes(cache_dir, url) is not None:
            return
        try:
            data = await _download_avatar_bytes_async(client, url)
            if data is not None:
                _save_avatar_bytes(cache_dir, url, data)
        except Exception as exc:
            warnings.warn(f"Avatar prefetch failed: {exc!s}", stacklevel=2)

    limits = httpx.Limits(max_connections=concurrency, max_keepalive_connections=concurrency)
    async with httpx.AsyncClient(limits=limits) as client:
        await asyncio.gather(*(_fetch(client, u) for u in urls))
