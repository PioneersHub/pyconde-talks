"""Management command for one-way (Pretalx -> Django) sync of speakers and talks via API."""

# ruff: noqa: BLE001

import argparse
import traceback
from typing import TYPE_CHECKING, Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandParser

from talks.management.commands._pretalx.client import PretalxClient, fetch_submissions
from talks.management.commands._pretalx.context import ImportContext
from talks.management.commands._pretalx.events import (
    get_or_create_event,
    maybe_update_event_name,
    resolve_event_slug,
    resolve_pretalx_url,
    split_pretalx_url,
)
from talks.management.commands._pretalx.images import TalkImageGenerator
from talks.management.commands._pretalx.mixins import ProcessingMixin
from talks.management.commands._pretalx.types import VerbosityLevel


if TYPE_CHECKING:
    from talks.management.commands._pretalx.pretalx_models import Submission


class Command(ProcessingMixin, BaseCommand):
    """
    Import talks and speakers from the Pretalx API into the Django database.

    Builds on :class:`~._pretalx.mixins.ProcessingMixin` (talk creation / update / deletion) and
    Django's ``BaseCommand``; submission fetching lives in :meth:`_fetch_submissions`.
    """

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
            "--detect-only",
            action="store_true",
            help="Detect changes against Pretalx and record them as PendingPretalxChange rows "
            "for admin review, without mutating Talks/Speakers/Rooms.",
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
            "--force-images",
            action="store_true",
            help="Regenerate talk social images for every existing talk, even when nothing changed."
            " Useful after a social-card template update. Ignored when --skip-images is set.",
        )
        parser.add_argument(
            "--no-avatars",
            action="store_true",
            help="Skip downloading and pasting speaker avatars on social cards",
        )
        parser.add_argument(
            "--use-cache",
            action=argparse.BooleanOptionalAction,
            default=settings.PICKLE_PRETALX_TALKS,
            help="Cache fetched submissions on disk for faster repeated local runs (dev only). "
            "Defaults to the PICKLE_PRETALX_TALKS setting; turn off with --no-use-cache.",
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
        Run the import pipeline.

        1. Resolve the event slug and ensure a Django ``Event`` row exists.
        2. Set up the Pretalx client and optionally sync the event name.
        3. Fetch all submissions from the API.
        4. Process each submission (create / update / delete talks).

        Flags ``--dry-run``, ``--no-update``, ``--skip-images`` are
        forwarded via the :class:`ImportContext` dataclass.
        """
        ctx = ImportContext.from_options(options, log_fn=self._log)

        event_slug = resolve_event_slug(ctx)
        if not event_slug:
            return

        event_obj, created = get_or_create_event(event_slug, ctx)

        pretalx_event_url = resolve_pretalx_url(
            ctx.pretalx_event_url,
            event_obj,
            event_slug,
        )
        ctx = ctx.evolve(event_obj=event_obj, pretalx_event_url=pretalx_event_url)
        pretalx_base_url, pretalx_event_slug = split_pretalx_url(pretalx_event_url)

        try:
            pretalx_client = PretalxClient(
                api_token=options["api_token"],
                api_base_url=pretalx_base_url,
            )

            if not ctx.event_name:
                maybe_update_event_name(
                    pretalx_client,
                    pretalx_event_slug,
                    event_obj,
                    ctx,
                    created=created,
                )

            submissions = self._fetch_submissions(
                pretalx_client,
                pretalx_event_slug,
                ctx,
            )
            if submissions is not None:
                self._process_submissions(submissions, ctx)

        except Exception as exc:
            ctx.log(
                f"An unexpected error occurred: {exc!s}",
                VerbosityLevel.NORMAL,
                "ERROR",
            )
            if ctx.verbosity.value >= VerbosityLevel.DEBUG.value:
                ctx.log(
                    traceback.format_exc(),
                    VerbosityLevel.DEBUG,
                    "ERROR",
                )

    def _fetch_submissions(
        self,
        pretalx_client: PretalxClient,
        pretalx_event_slug: str,
        ctx: ImportContext,
    ) -> list[Submission] | None:
        """Fetch submissions from the API; return ``None`` on failure (logged, not raised)."""
        event_slug = ctx.event_slug or pretalx_event_slug
        ctx.log(
            f"Fetching talks from Pretalx event '{event_slug}'...",
            VerbosityLevel.NORMAL,
        )
        try:
            submissions = fetch_submissions(pretalx_client, pretalx_event_slug, ctx)
        except Exception as exc:
            ctx.log(
                f"Failed to fetch talks: {exc!s}",
                VerbosityLevel.NORMAL,
                "ERROR",
            )
            return None

        ctx.log(
            f"Fetched {len(submissions)} talks from Pretalx event '{event_slug}'",
            VerbosityLevel.NORMAL,
            style="SUCCESS",
        )
        return submissions
