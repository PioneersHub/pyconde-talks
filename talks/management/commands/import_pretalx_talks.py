"""Management command for one-way (Pretalx → Django) sync of speakers and talks via API."""

# ruff: noqa: BLE001

import traceback
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar, cast

import httpx
from django.conf import settings
from django.core.management.base import BaseCommand, CommandParser
from django.utils import timezone
from pydantic import ValidationError
from pytanis import PretalxClient
from pytanis.config import PretalxCfg
from pytanis.pretalx.models import State
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from events.models import Event
from talks.management.commands._pretalx.avatars import (
    get_avatar_cache_dir,
    prefetch_avatar_urls,
)
from talks.management.commands._pretalx.images import TalkImageGenerator
from talks.management.commands._pretalx.rooms import batch_create_rooms
from talks.management.commands._pretalx.speakers import (
    batch_create_or_update_speakers,
)
from talks.management.commands._pretalx.submission import (
    SubmissionData,
    submission_is_announcement,
    submission_is_lightning_talk,
)
from talks.management.commands._pretalx.types import PytanisCfg, VerbosityLevel
from talks.models import MAX_TALK_TITLE_LENGTH, Room, Speaker, Talk


if TYPE_CHECKING:
    from collections.abc import Sequence

    from pytanis.pretalx.models import Submission, SubmissionSpeaker


class Command(BaseCommand):
    """Fetch talks and speakers from Pretalx API and save them to the database."""

    help = "Import talks and speakers from Pretalx"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Initialize the command with a talk image generator."""
        super().__init__(*args, **kwargs)
        self._image_generator = TalkImageGenerator()

    # ------------------------------------------------------------------
    # Argument parsing
    # ------------------------------------------------------------------

    def add_arguments(self, parser: CommandParser) -> None:
        """Add command line arguments."""
        parser.add_argument(
            "--pretalx-event-url",
            type=str,
            default="",
            help="Base Event URL for Pretalx (used to build talk links). "
            "Falls back to the Event's pretalx_url field.",
        )
        parser.add_argument(
            "--event-slug",
            type=str,
            default=getattr(settings, "DEFAULT_EVENT", ""),
            help="Event slug in the Django app (not necessarily the same as the Pretalx slug).",
        )
        parser.add_argument(
            "--event-name",
            type=str,
            default="",
            help="Human-readable name for the event (used when creating a new Event).",
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

    # ------------------------------------------------------------------
    # Main entry-point
    # ------------------------------------------------------------------

    def handle(self, *args: Any, **options: Any) -> None:  # noqa: ARG002
        """
        Execute the command to import talks from Pretalx.

        - If no event slug is provided via CLI and DEFAULT_EVENT is not set, it will be derived from
        the pretalx_event_url.
        - If the event slug does not exist in the database yet, it will be created automatically.
        - The --pretalx-event-url CLI flag takes precedence over the Event's pretalx_url field.
        """
        event_slug: str = options["event_slug"]
        event_name: str = options["event_name"]
        pretalx_event_url: str = options["pretalx_event_url"]
        verbosity = VerbosityLevel(options["verbosity"])
        max_retries: int = options["max_retries"]

        if not event_slug and not pretalx_event_url:
            self._log(
                "No event slug provided and no Pretalx event URL provided. Cannot proceed.",
                verbosity,
                VerbosityLevel.NORMAL,
                "ERROR",
            )
            return

        if not event_slug:
            event_slug = pretalx_event_url.rstrip("/").split("/")[-1]
            self._log(
                f"No event slug provided, derived from Pretalx URL: '{event_slug}'",
                verbosity,
                VerbosityLevel.NORMAL,
                "WARNING",
            )

        # Resolve or create the Event object for this slug
        event_obj, created = Event.objects.get_or_create(
            slug=event_slug,
            defaults={
                "name": event_name or event_slug,
                "year": timezone.now().year,
                "pretalx_url": pretalx_event_url,
            },
        )
        options["_event_obj"] = event_obj
        if created:
            self._log(
                f"Created new Event '{event_slug}'",
                verbosity,
                VerbosityLevel.NORMAL,
                "SUCCESS",
            )

        # Resolve pretalx_event_url: CLI flag takes precedence, then Event field
        if not pretalx_event_url:
            pretalx_event_url = event_obj.pretalx_url or f"https://pretalx.com/{event_slug}"
            options["pretalx_event_url"] = pretalx_event_url

        # Split pretalx_event_url into base URL and event slug because Pytanis needs them separately
        pretalx_base_url, pretalx_event_slug = pretalx_event_url.rstrip("/").rsplit("/", 1)

        try:
            pretalx_client = self._setup_pretalx_client(
                api_token=options["api_token"],
                api_base_url=pretalx_base_url,
            )

            # If event name is not provided, try to fetch it from the API
            if not event_name:
                event = pretalx_client.event(pretalx_event_slug)
                if (
                    hasattr(event, "name")
                    and event.name
                    and hasattr(event.name, "en")
                    and event.name.en
                ):
                    event_name = event.name.en

                self._log(
                    f"Fetched event name from Pretalx API: '{event_name}'",
                    verbosity,
                    VerbosityLevel.NORMAL,
                )
                if created and event_name and event_name != event_slug:
                    event_obj.name = event_name
                    event_obj.save()
                    self._log(
                        f"Updated Event name to '{event_name}'",
                        verbosity,
                        VerbosityLevel.NORMAL,
                        "SUCCESS",
                    )

            # Fetch talks from API
            self._log(
                f"Fetching talks from Pretalx event '{event_slug}'...",
                verbosity,
                VerbosityLevel.NORMAL,
            )
            try:
                talk_count, submissions = self._fetch_talks_with_retry(
                    pretalx_client,
                    pretalx_event_slug,
                    max_retries,
                    verbosity,
                )
                self._log(
                    f"Fetched {talk_count} talks from Pretalx event '{event_slug}'",
                    verbosity,
                    VerbosityLevel.NORMAL,
                    style="SUCCESS",
                )
            except Exception as exc:
                self._log(
                    f"Failed to fetch talks: {exc!s}",
                    verbosity,
                    VerbosityLevel.NORMAL,
                    "ERROR",
                )
                return

            self._process_submissions(list(submissions), options)

        except Exception as exc:
            self._log(
                f"An unexpected error occurred: {exc!s}",
                verbosity,
                VerbosityLevel.NORMAL,
                "ERROR",
            )
            if verbosity.value >= VerbosityLevel.DEBUG.value:
                self._log(
                    traceback.format_exc(),
                    verbosity,
                    VerbosityLevel.DEBUG,
                    "ERROR",
                )

    # ------------------------------------------------------------------
    # Logging helper
    # ------------------------------------------------------------------

    def _log(
        self,
        message: str,
        verbosity: VerbosityLevel,
        min_level: VerbosityLevel,
        style: str | None = None,
    ) -> None:
        """Log *message* if *verbosity* ≥ *min_level*."""
        if verbosity.value < min_level.value:
            return
        if style == "SUCCESS":
            self.stdout.write(self.style.SUCCESS(message))
        elif style == "WARNING":
            self.stdout.write(self.style.WARNING(message))
        elif style == "ERROR":
            self.stderr.write(self.style.ERROR(message))
        else:
            self.stdout.write(message)

    # ------------------------------------------------------------------
    # Pretalx client & fetch
    # ------------------------------------------------------------------

    @staticmethod
    def _setup_pretalx_client(
        api_token: str,
        api_base_url: str = "https://pretalx.com/",
        timeout: int | None = None,
        calls_per_second: int = 2,
    ) -> PretalxClient:
        """Set up and configure the Pretalx client."""
        pretalx_cfg = PretalxCfg(api_token=api_token, api_base_url=api_base_url, timeout=timeout)
        config = PytanisCfg(Pretalx=pretalx_cfg)
        client = PretalxClient(config)  # type: ignore[arg-type]
        client.set_throttling(calls=calls_per_second, seconds=1)
        return client

    def _fetch_talks_with_retry(
        self,
        pretalx: PretalxClient,
        pretalx_event_slug: str,
        max_retries: int,
        verbosity: VerbosityLevel,
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
                count, submissions = pretalx.submissions(pretalx_event_slug)
                return (count, list(submissions))

            import pickle  # nosec: B403  # noqa: PLC0415

            pickle_file = Path(".pretalx_cache")

            if pickle_file.exists():
                try:
                    with pickle_file.open("rb") as f:
                        return cast(
                            "tuple[int, list[Submission]]",
                            pickle.load(f),  # noqa: S301  # nosec: B301
                        )
                except (pickle.PickleError, OSError):  # fmt: skip
                    pass

            # Pickle doesn't exist or failed to load, fetch from API and cache it
            count, submissions = pretalx.submissions(pretalx_event_slug)
            result = (count, list(submissions))
            try:
                with pickle_file.open("wb") as wb_file:
                    pickle.dump(result, wb_file)
            except OSError:
                self._log(
                    f"Failed to cache Pretalx talks to {pickle_file}",
                    verbosity,
                    VerbosityLevel.NORMAL,
                    "WARNING",
                )
            return result

        return _retry_fetch_talks()

    # ------------------------------------------------------------------
    # Submission processing (orchestration)
    # ------------------------------------------------------------------

    def _process_submissions(
        self,
        submissions: Sequence[Submission],
        options: dict[str, Any],
    ) -> None:
        """Process a list of submissions."""
        verbosity = VerbosityLevel(options["verbosity"])
        dry_run = options.get("dry_run", False)
        no_update = options.get("no_update", False)

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

        # Best-effort avatar prefetch
        try:
            self._prefetch_avatars_for_submissions(submissions, options)
        except Exception as exc:
            self._log(
                f"Avatar prefetch failed (continuing without cache): {exc!s}",
                verbosity,
                VerbosityLevel.DETAILED,
                "WARNING",
            )

        if not dry_run:
            batch_create_rooms(submissions, options, log_fn=self._log)
            batch_create_or_update_speakers(
                submissions,
                options,
                log_fn=self._log,
            )

        for idx, submission in enumerate(submissions):
            self._log(
                f"Processing {idx + 1}/{stats['total']}: {submission.title}",
                verbosity,
                VerbosityLevel.DETAILED,
            )
            try:
                if not self._is_valid_submission(submission, verbosity):
                    stats["skipped"] += 1
                    continue
                result = self._process_single_submission(
                    submission,
                    options,
                )
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

        self._log(
            f"Import complete: {stats['created']} created, {stats['updated']} updated, "
            f"{stats['deleted']} deleted, {stats['skipped']} skipped, "
            f"{stats['failed']} failed, {stats['total']} total",
            verbosity,
            VerbosityLevel.NORMAL,
            "SUCCESS",
        )

    # ------------------------------------------------------------------
    # Avatar prefetch
    # ------------------------------------------------------------------

    @staticmethod
    def _prefetch_avatars_for_submissions(
        submissions: Sequence[Submission],
        options: dict[str, Any],
    ) -> None:
        """Collect unique avatar URLs and prefetch them into cache."""
        if options.get("skip_images", False):
            return
        urls: set[str] = set()
        for sub in submissions:
            if getattr(sub, "state", None) not in {
                State.accepted,
                State.confirmed,
            }:
                continue
            for sp in getattr(sub, "speakers", None) or []:
                url = getattr(sp, "avatar_url", None) or ""
                if url:
                    urls.add(url)
        if urls:
            prefetch_avatar_urls(urls, get_avatar_cache_dir())

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def _is_valid_submission(
        self,
        submission: Submission,
        verbosity: VerbosityLevel,
    ) -> bool:
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
            if submission_is_lightning_talk(
                submission,
            ) or submission_is_announcement(submission):
                valid = True
                self._log(
                    f"Submission {submission.code} has no speakers",
                    verbosity,
                    VerbosityLevel.NORMAL,
                    "WARNING",
                )
            else:
                valid = settings.IMPORT_TALKS_WITHOUT_SPEAKERS
                self._log(
                    f"Submission {submission.code} has no speakers",
                    verbosity,
                    VerbosityLevel.NORMAL,
                    "WARNING",
                )

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

    # ------------------------------------------------------------------
    # Single submission processing
    # ------------------------------------------------------------------

    def _process_single_submission(
        self,
        submission: Submission,
        options: dict[str, Any],
    ) -> str:
        """Process a single submission and return the result status."""
        verbosity = VerbosityLevel(options["verbosity"])
        dry_run = options.get("dry_run", False)
        no_update = options.get("no_update", False)

        data = SubmissionData(
            submission,
            options.get("pretalx_event_url", ""),
        )
        existing_talk = Talk.objects.filter(pretalx_link=data.pretalx_link).first()

        # Delete cancelled talks
        if submission.state not in (State.confirmed, State.accepted):
            if existing_talk:
                self._log(
                    f"Talk {data.title} changed state to {submission.state}. Deleting",
                    verbosity,
                    VerbosityLevel.NORMAL,
                    "WARNING",
                )
                if not dry_run:
                    existing_talk.delete()
                return "deleted"
            return "skipped"

        # Update existing
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
                self._update_talk(
                    existing_talk,
                    data,
                    submission.speakers,
                    options,
                )
            return "updated"

        # Create new
        self._log(
            f"Creating new talk: {data.title}",
            verbosity,
            VerbosityLevel.DETAILED,
        )
        if not dry_run:
            talk = self._create_talk(data, options)
            self._add_speakers_to_talk(talk, submission.speakers, options)
            if not options.get("skip_images", False):
                self._generate_talk_image(talk, options)
        return "created"

    # ------------------------------------------------------------------
    # Talk CRUD
    # ------------------------------------------------------------------

    def _create_talk(self, data: SubmissionData, options: dict[str, Any]) -> Talk:
        """Create a new Talk from submission data."""
        verbosity = VerbosityLevel(
            options.get("verbosity", VerbosityLevel.NORMAL.value),
        )
        presentation_type = self._map_presentation_type(
            data.submission_type,
            data.code,
            verbosity,
        )
        room = self._get_or_create_room(data.room, options) if data.room else None

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
            event=options.get("_event_obj"),
        )
        self._log(
            f"Created talk: {data.title}",
            verbosity,
            VerbosityLevel.DETAILED,
            "SUCCESS",
        )
        return talk

    def _update_talk(
        self,
        talk: Talk,
        data: SubmissionData,
        speakers: list[SubmissionSpeaker],
        options: dict[str, Any],
    ) -> None:
        """Update an existing Talk with data from a Submission."""
        verbosity = VerbosityLevel(
            options.get("verbosity", VerbosityLevel.NORMAL.value),
        )

        talk.title = data.title
        talk.abstract = data.abstract
        talk.description = data.description
        talk.start_time = data.start_time

        if data.duration:
            talk.duration = data.duration
        talk.room = self._get_or_create_room(data.room, options) if data.room else None
        talk.track = data.track
        if data.image_url:
            talk.external_image_url = data.image_url
        talk.presentation_type = self._map_presentation_type(
            data.submission_type,
            data.code,
            verbosity,
        )
        # Ensure existing talk is linked to the event
        event_obj = options.get("_event_obj")
        if event_obj and talk.event != event_obj:
            talk.event = event_obj
        talk.save()

        self._log(
            f"Updated talk: {talk.title}",
            verbosity,
            VerbosityLevel.DETAILED,
            "SUCCESS",
        )
        self._update_talk_speakers(talk, speakers, options)

    # ------------------------------------------------------------------
    # Speaker management
    # ------------------------------------------------------------------

    def _add_speakers_to_talk(
        self,
        talk: Talk,
        speakers: list[SubmissionSpeaker],
        options: dict[str, Any],
    ) -> None:
        """Add speakers to a talk."""
        verbosity = VerbosityLevel(
            options.get("verbosity", VerbosityLevel.NORMAL.value),
        )
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
        """Update the speakers for a talk, adding new and removing old."""
        verbosity = VerbosityLevel(
            options.get("verbosity", VerbosityLevel.NORMAL.value),
        )
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

        current_ids = set(
            talk.speakers.all().values_list("pretalx_id", flat=True),
        )
        new_ids = {sp.code for sp in submission_speakers}

        to_add = [
            self._get_or_create_speaker(sp, options)
            for sp in submission_speakers
            if sp.code not in current_ids
        ]
        if to_add:
            talk.speakers.add(*to_add)
            self._log(
                f"Added {len(to_add)} speakers to talk: {talk.title}",
                verbosity,
                VerbosityLevel.DETAILED,
                "SUCCESS",
            )

        ids_to_remove = current_ids - new_ids
        if ids_to_remove:
            objs = talk.speakers.filter(pretalx_id__in=ids_to_remove)
            removed = objs.count()
            talk.speakers.remove(*objs)
            self._log(
                f"Removed {removed} speakers from talk: {talk.title}",
                verbosity,
                VerbosityLevel.DETAILED,
                "SUCCESS",
            )

    def _get_or_create_speaker(
        self,
        speaker_data: SubmissionSpeaker,
        options: dict[str, Any],
    ) -> Speaker:
        """Get an existing speaker or create a new one."""
        verbosity = VerbosityLevel(
            options.get("verbosity", VerbosityLevel.NORMAL.value),
        )
        no_update = options.get("no_update", False)
        dry_run = options.get("dry_run", False)

        existing = Speaker.objects.filter(pretalx_id=speaker_data.code).first()
        if existing:
            self._maybe_update_speaker(
                existing,
                speaker_data,
                verbosity,
                no_update=no_update,
                dry_run=dry_run,
            )
            return existing

        if dry_run:
            self._log(
                f"Would create speaker: {speaker_data.name} (dry run)",
                verbosity,
                VerbosityLevel.DETAILED,
                "SUCCESS",
            )
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

    def _maybe_update_speaker(
        self,
        existing: Speaker,
        speaker_data: SubmissionSpeaker,
        verbosity: VerbosityLevel,
        *,
        no_update: bool,
        dry_run: bool,
    ) -> None:
        """Update *existing* speaker if data changed and flags allow it."""
        if no_update:
            self._log(
                f"Skipping update for existing speaker: {speaker_data.name} (--no-update)",
                verbosity,
                VerbosityLevel.DETAILED,
                "WARNING",
            )
            return
        if dry_run:
            return

        bio = speaker_data.biography or ""
        avatar = speaker_data.avatar_url or ""
        if (
            existing.name == speaker_data.name
            and existing.biography == bio
            and existing.avatar == avatar
        ):
            return

        existing.name = speaker_data.name
        existing.biography = bio
        existing.avatar = avatar
        existing.save()
        self._log(
            f"Updated speaker: {speaker_data.name}",
            verbosity,
            VerbosityLevel.DETAILED,
            "SUCCESS",
        )

    # ------------------------------------------------------------------
    # Room management
    # ------------------------------------------------------------------

    def _get_or_create_room(
        self,
        room_name: str,
        options: dict[str, Any],
    ) -> Room | None:
        """Get an existing room or create a new one."""
        if not room_name:
            return None
        verbosity = VerbosityLevel(
            options.get("verbosity", VerbosityLevel.NORMAL.value),
        )
        dry_run = options.get("dry_run", False)

        existing = Room.objects.filter(name=room_name).first()
        if existing:
            self._log(
                f"Using existing room: {room_name}",
                verbosity,
                VerbosityLevel.DETAILED,
            )
            return existing

        if dry_run:
            self._log(
                f"Would create room: {room_name} (dry run)",
                verbosity,
                VerbosityLevel.DETAILED,
                "SUCCESS",
            )
            return Room(name=room_name, description="")

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

    # ------------------------------------------------------------------
    # Presentation type mapping
    # ------------------------------------------------------------------

    _TYPE_MAPPING: ClassVar[dict[str, str]] = {
        "Invited Talk": "Talk",
        "Keynote": "Keynote",
        "Kids Workshop": "Kids",
        "Lightning Talks": "Lightning",
        "Panel": "Panel",
        "Plenary Session [Organizers]": "Plenary",
        "Sponsored Talk (Keystone)": "Tutorial",
        "Sponsored Talk (long)": "Talk",
        "Sponsored Talk": "Talk",
        "Talk (long) [Sponsored]": "Talk",
        "Talk (long)": "Talk",
        "Talk [Sponsored]": "Talk",
        "Talk": "Talk",
        "Tutorial [Sponsored]": "Tutorial",
        "Tutorial": "Tutorial",
    }

    def _map_presentation_type(
        self,
        submission_type: str | None,
        submission_code: str,
        verbosity: VerbosityLevel,
    ) -> str:
        """Map Pretalx submission type to Django model presentation type."""
        if not submission_type:
            self._log(
                f"Empty presentation type for submission {submission_code}, defaulting to 'Talk'",
                verbosity,
                VerbosityLevel.DETAILED,
                "WARNING",
            )
            return Talk.PresentationType.TALK

        mapped = self._TYPE_MAPPING.get(submission_type)
        if mapped is None:
            self._log(
                f"Unrecognized presentation type '{submission_type}' for "
                f"submission {submission_code}, defaulting to 'Talk'",
                verbosity,
                VerbosityLevel.DETAILED,
                "WARNING",
            )
            return Talk.PresentationType.TALK

        return mapped

    # ------------------------------------------------------------------
    # Image generation (delegates to TalkImageGenerator)
    # ------------------------------------------------------------------

    def _generate_talk_image(
        self,
        talk: Talk,
        options: dict[str, Any],
    ) -> None:
        """Generate a social card image for *talk*."""
        self._image_generator.generate(talk, options)
