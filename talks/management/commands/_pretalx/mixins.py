"""
Command mixins that compose with :class:`~django.core.management.base.BaseCommand`.

* :class:`LoggingMixin` -- structured, verbosity-aware logging.
* :class:`FetchMixin` -- fetches submissions from the Pretalx API.
* :class:`ProcessingMixin` -- turns submissions into Django ``Talk`` rows.
"""

# ruff: noqa: BLE001

import traceback
from typing import TYPE_CHECKING, Literal

from pytanis.pretalx.models import State

from talks.management.commands._pretalx.avatars import prefetch_avatars_for_submissions
from talks.management.commands._pretalx.client import fetch_talks_with_retry
from talks.management.commands._pretalx.rooms import batch_create_rooms
from talks.management.commands._pretalx.speakers import batch_create_or_update_speakers
from talks.management.commands._pretalx.submission import SubmissionData
from talks.management.commands._pretalx.talks import (
    add_speakers_to_talk,
    create_talk,
    update_talk,
)
from talks.management.commands._pretalx.types import VerbosityLevel
from talks.management.commands._pretalx.validation import is_valid_submission
from talks.models import Talk


if TYPE_CHECKING:
    from collections.abc import Sequence

    from django.core.management.base import OutputWrapper
    from django.core.management.color import Style
    from pytanis import PretalxClient
    from pytanis.pretalx.models import Submission, SubmissionSpeaker

    from talks.management.commands._pretalx.context import ImportContext
    from talks.management.commands._pretalx.images import TalkImageGenerator

#: Keys used in the stats dict returned by :func:`_new_stats`.
type _StatKey = Literal[
    "total",
    "created",
    "updated",
    "deleted",
    "skipped",
    "failed",
]

#: Result status returned by per-submission handlers.
type _ResultStatus = Literal["created", "updated", "deleted", "skipped"]


# ------------------------------------------------------------------
# Logging
# ------------------------------------------------------------------


class LoggingMixin:
    """
    Verbosity-aware logging for ``BaseCommand`` subclasses.

    Relies on ``stdout``, ``stderr``, and ``style`` attributes provided by
    :class:`~django.core.management.base.BaseCommand`.
    """

    stdout: OutputWrapper
    stderr: OutputWrapper
    style: Style

    def _log(
        self,
        message: str,
        verbosity: VerbosityLevel,
        min_level: VerbosityLevel,
        style: str | None = None,
    ) -> None:
        """
        Write *message* to stdout/stderr when *verbosity* >= *min_level*.

        Parameters
        ----------
        message:
            Text to log.
        verbosity:
            Current command verbosity (from ``ImportContext``).
        min_level:
            Minimum level at which the message is emitted.
        style:
            Optional Django style name (``"SUCCESS"``, ``"WARNING"``,
            ``"ERROR"``).  ``"ERROR"`` directs output to *stderr*.

        """
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
# API fetching
# ------------------------------------------------------------------


class FetchMixin(LoggingMixin):
    """
    Fetches submissions from the Pretalx API.

    Provides :meth:`_fetch_submissions` for use in ``Command.handle()``.
    """

    def _fetch_submissions(
        self,
        pretalx_client: PretalxClient,
        pretalx_event_slug: str,
        ctx: ImportContext,
    ) -> list[Submission] | None:
        """Fetch submissions from the API; return ``None`` on failure."""
        event_slug = ctx.event_slug or pretalx_event_slug

        ctx.log(
            f"Fetching talks from Pretalx event '{event_slug}'...",
            VerbosityLevel.NORMAL,
        )
        try:
            talk_count, submissions = fetch_talks_with_retry(
                pretalx_client,
                pretalx_event_slug,
                ctx,
            )
            ctx.log(
                f"Fetched {talk_count} talks from Pretalx event '{event_slug}'",
                VerbosityLevel.NORMAL,
                style="SUCCESS",
            )
        except Exception as exc:
            ctx.log(
                f"Failed to fetch talks: {exc!s}",
                VerbosityLevel.NORMAL,
                "ERROR",
            )
            return None

        return list(submissions)


# ------------------------------------------------------------------
# Submission processing
# ------------------------------------------------------------------


class ProcessingMixin(LoggingMixin):
    """
    Turn Pretalx submissions into Django :class:`~talks.models.Talk` rows.

    Expects the host ``Command.__init__`` to set ``_image_generator``.
    """

    _image_generator: TalkImageGenerator

    def _process_submissions(
        self,
        submissions: Sequence[Submission],
        ctx: ImportContext,
    ) -> None:
        """Import every submission: validate, create/update/delete talks, generate images."""
        stats = _new_stats(len(submissions))

        if ctx.dry_run:
            ctx.log(
                "DRY RUN: No changes will be saved to the database",
                VerbosityLevel.NORMAL,
                "WARNING",
            )
        if ctx.no_update:
            ctx.log(
                "NO UPDATE: Existing talks and speakers will not be updated",
                VerbosityLevel.NORMAL,
                "WARNING",
            )

        # Best-effort avatar prefetch
        try:
            prefetch_avatars_for_submissions(submissions, ctx)
        except Exception as exc:
            ctx.log(
                f"Avatar prefetch failed (continuing without cache): {exc!s}",
                VerbosityLevel.DETAILED,
                "WARNING",
            )

        if not ctx.dry_run:
            batch_create_rooms(submissions, ctx)
            batch_create_or_update_speakers(submissions, ctx)

        for idx, submission in enumerate(submissions):
            ctx.log(
                f"Processing {idx + 1}/{stats['total']}: {submission.title}",
                VerbosityLevel.DETAILED,
            )
            try:
                if not is_valid_submission(submission, ctx):
                    stats["skipped"] += 1
                    continue
                result = self._process_single_submission(submission, ctx)
                stats[result] += 1
            except Exception as exc:
                stats["failed"] += 1
                ctx.log(
                    f"Error processing submission {submission.code}: {exc!s}",
                    VerbosityLevel.NORMAL,
                    "ERROR",
                )
                if ctx.verbosity.value >= VerbosityLevel.DEBUG.value:
                    self.stderr.write(traceback.format_exc())

        ctx.log(
            f"Import complete: {stats['created']} created, {stats['updated']} updated, "
            f"{stats['deleted']} deleted, {stats['skipped']} skipped, "
            f"{stats['failed']} failed, {stats['total']} total",
            VerbosityLevel.NORMAL,
            "SUCCESS",
        )

    # ------------------------------------------------------------------
    # Single submission processing
    # ------------------------------------------------------------------

    def _process_single_submission(
        self,
        submission: Submission,
        ctx: ImportContext,
    ) -> _ResultStatus:
        """
        Route a single submission to the appropriate handler.

        Returns one of ``"created"``, ``"updated"``, ``"deleted"``, or
        ``"skipped"`` for stats tracking.
        """
        data = SubmissionData(submission, ctx.pretalx_event_url)
        existing_talk = Talk.objects.filter(pretalx_link=data.pretalx_link).first()

        # Delete cancelled talks
        if submission.state not in (State.confirmed, State.accepted):
            return self._handle_cancelled(existing_talk, data, ctx)

        # Update existing
        if existing_talk:
            return self._handle_existing(existing_talk, data, submission.speakers, ctx)

        # Create new
        return self._handle_new(data, submission.speakers, ctx)

    def _handle_cancelled(
        self,
        existing_talk: Talk | None,
        data: SubmissionData,
        ctx: ImportContext,
    ) -> _ResultStatus:
        """Delete *existing_talk* when its submission is no longer confirmed/accepted."""
        if not existing_talk:
            return "skipped"
        ctx.log(
            f"Talk {data.title} is no longer confirmed/accepted. Deleting",
            VerbosityLevel.NORMAL,
            "WARNING",
        )
        if not ctx.dry_run:
            existing_talk.delete()
        return "deleted"

    def _handle_existing(
        self,
        existing_talk: Talk,
        data: SubmissionData,
        speakers: list[SubmissionSpeaker],
        ctx: ImportContext,
    ) -> _ResultStatus:
        """Update *existing_talk* with fresh data, or skip when ``--no-update`` is set."""
        if ctx.no_update:
            ctx.log(
                f"Skipping update for existing talk: {data.title}",
                VerbosityLevel.DETAILED,
                "WARNING",
            )
            return "skipped"
        ctx.log(
            f"Updating existing talk: {data.title}",
            VerbosityLevel.DETAILED,
            "WARNING",
        )
        if not ctx.dry_run:
            update_talk(existing_talk, data, speakers, ctx)
        return "updated"

    def _handle_new(
        self,
        data: SubmissionData,
        speakers: list[SubmissionSpeaker],
        ctx: ImportContext,
    ) -> _ResultStatus:
        """Create a new :class:`~talks.models.Talk` and attach speakers + image."""
        ctx.log(
            f"Creating new talk: {data.title}",
            VerbosityLevel.DETAILED,
        )
        if not ctx.dry_run:
            talk = create_talk(data, ctx)
            add_speakers_to_talk(talk, speakers, ctx)
            if not ctx.skip_images:
                self._image_generator.generate(talk, ctx)
        return "created"


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _new_stats(total: int) -> dict[_StatKey, int]:
    """Return a zero-initialized stats counter with *total* pre-filled."""
    return {
        "total": total,
        "created": 0,
        "updated": 0,
        "deleted": 0,
        "skipped": 0,
        "failed": 0,
    }
