"""Management command for one-way (Pretalx » Django) sync of speakers and talks via API."""
# ruff: noqa: BLE001

import traceback
from collections.abc import Iterator
from datetime import timedelta
from enum import Enum
from typing import Any

import httpx
from django.conf import settings
from django.core.management.base import BaseCommand, CommandParser
from pydantic import BaseModel, ValidationError
from pytanis import PretalxClient
from pytanis.config import PretalxCfg
from pytanis.pretalx.models import (
    State,
    Submission,
    SubmissionSpeaker,
)
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
        base_url = (
            pretalx_base_url or getattr(settings, "PRETALX_BASE_URL", "https://pretalx.com")
        ).rstrip("/")
        self.pretalx_link = f"{base_url}/{event_slug}/talk/{submission.code}"
        self.image_url = getattr(submission, "image", "") or ""

        # Extract room safely
        self.room = ""
        if (
            hasattr(submission, "slot")
            and submission.slot
            and hasattr(submission.slot, "room")
            and submission.slot.room
            and hasattr(submission.slot.room, "en")
            and submission.slot.room.en
        ):
            self.room = submission.slot.room.en[:MAX_ROOM_NAME_LENGTH]

        # Extract track safely
        self.track = ""
        if (
            hasattr(submission, "track")
            and submission.track
            and hasattr(submission.track, "en")
            and submission.track.en
        ):
            self.track = submission.track.en[:MAX_TRACK_NAME_LENGTH]

        # Extract start time safely
        self.start_time = FAR_FUTURE
        if (
            hasattr(submission, "slot")
            and submission.slot
            and hasattr(submission.slot, "start")
            and submission.slot.start
        ):
            self.start_time = submission.slot.start

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
            self.submission_type = submission.submission_type.en


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
            except Exception as e:
                self.stderr.write(self.style.ERROR(f"Failed to fetch talks: {e!s}"))
                return

            # Process submissions
            self._process_submissions(list(submissions), event_slug, options)

        except Exception as e:
            self.stderr.write(self.style.ERROR(f"An unexpected error occurred: {e!s}"))
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
        def _retry_fetch_talks() -> tuple[int, Iterator[Submission]]:
            return pretalx.submissions(event_slug)

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

            except Exception as e:
                stats["failed"] += 1
                self._log(
                    f"Error processing submission {submission.code}: {e!s}",
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
            valid = False
            self._log(
                f"Submission {submission.code} has no speakers",
                verbosity,
                VerbosityLevel.NORMAL,
                "ERROR",
            )

        return valid

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
                    or existing_speaker.avatar != (speaker_data.avatar or "")
                    or existing_speaker.name != speaker_data.name
                )
            ):
                existing_speaker.name = speaker_data.name
                existing_speaker.biography = speaker_data.biography or ""
                existing_speaker.avatar = speaker_data.avatar or ""
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
                avatar=speaker_data.avatar or "",
                pretalx_id=speaker_data.code,
            )

        speaker = Speaker.objects.create(
            name=speaker_data.name,
            biography=speaker_data.biography or "",
            avatar=speaker_data.avatar or "",
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
            "Sponsored Talk (Keystone)": Talk.PresentationType.TUTORIAL,
            "Sponsored Talk (long)": Talk.PresentationType.TALK,
            "Sponsored Talk": Talk.PresentationType.TALK,
            "Talk (long)": Talk.PresentationType.TALK,
            "Talk": Talk.PresentationType.TALK,
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
