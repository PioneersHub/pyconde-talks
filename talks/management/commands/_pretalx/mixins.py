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
from talks.management.commands._pretalx.digest import maybe_send_digest
from talks.management.commands._pretalx.images import (
    image_is_older_than,
    latest_template_mtime,
)
from talks.management.commands._pretalx.pending import (
    build_submission_payload,
    diff_speakers,
    record_pending_change,
    serialize_field_diffs,
)
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
from talks.models import PendingPretalxChange, Talk


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
    "unchanged",
    "detected",
    "deleted",
    "skipped",
    "failed",
]

#: Result status returned by per-submission handlers.
type _ResultStatus = Literal[
    "created",
    "updated",
    "unchanged",
    "detected",
    "deleted",
    "skipped",
]


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
    #: Pending rows created or refreshed during this run; used for the email digest.
    #: Populated by the detect-only handlers in :meth:`_detect_update` / ``_handle_new`` /
    #: ``_handle_cancelled`` so the digest reflects *this* run's diffs, not the full
    #: outstanding queue. Initialized fresh in :meth:`_process_submissions`.
    _detected_changes: list[PendingPretalxChange] = []  # noqa: RUF012
    #: ``pretalx_id`` set of speakers whose name/avatar changed during this import run.
    #: Populated by :meth:`_process_submissions` from
    #: :func:`~_pretalx.speakers.batch_create_or_update_speakers` and consulted by
    #: :meth:`_handle_existing` to decide when an "unchanged" talk still needs a fresh
    #: social card. Defaults to ``frozenset()`` so direct ``_handle_existing`` callers
    #: (notably the unit tests) do not need to populate it.
    _speakers_with_visual_change: frozenset[str] = frozenset()
    #: Latest mtime (epoch seconds) across the event's social-card template PNGs.
    #: ``None`` means "no templates / nothing to compare against". Set per
    #: :meth:`_process_submissions` call.
    _template_mtime: float | None = None

    def _process_submissions(
        self,
        submissions: Sequence[Submission],
        ctx: ImportContext,
    ) -> None:
        """Import every submission: validate, create/update/delete talks, generate images."""
        stats = _new_stats(len(submissions))
        # Fresh per-run buffer so a re-run doesn't email last run's diffs.
        self._detected_changes = []

        self._log_mode_banners(ctx)
        self._prefetch_avatars(submissions, ctx)
        self._run_bulk_upserts(submissions, ctx)
        # Cache the template mtime once so each talk can compare against it without
        # restating the filesystem repeatedly.
        self._template_mtime = latest_template_mtime(ctx)

        for idx, submission in enumerate(submissions):
            ctx.log(
                f"Processing {idx + 1}/{stats['total']}: {submission.title}",
                VerbosityLevel.DETAILED,
            )
            self._process_one_with_stats(submission, ctx, stats)

        ctx.log(
            f"Import complete: {stats['created']} created, {stats['updated']} updated, "
            f"{stats['unchanged']} unchanged, {stats['detected']} detected, "
            f"{stats['deleted']} deleted, {stats['skipped']} skipped, "
            f"{stats['failed']} failed, {stats['total']} total",
            VerbosityLevel.NORMAL,
            "SUCCESS",
        )

        if ctx.detect_only and maybe_send_digest(self._detected_changes, ctx):
            ctx.log(
                f"Sent Pretalx digest for {len(self._detected_changes)} change(s)",
                VerbosityLevel.NORMAL,
                "SUCCESS",
            )

    @staticmethod
    def _log_mode_banners(ctx: ImportContext) -> None:
        """Emit the warning banners for ``--dry-run`` / ``--detect-only`` / ``--no-update``."""
        if ctx.dry_run:
            ctx.log(
                "DRY RUN: No changes will be saved to the database",
                VerbosityLevel.NORMAL,
                "WARNING",
            )
        if ctx.detect_only:
            ctx.log(
                "DETECT-ONLY: changes will be recorded as PendingPretalxChange rows only",
                VerbosityLevel.NORMAL,
                "WARNING",
            )
        if ctx.no_update:
            ctx.log(
                "NO UPDATE: Existing talks and speakers will not be updated",
                VerbosityLevel.NORMAL,
                "WARNING",
            )

    @staticmethod
    def _prefetch_avatars(
        submissions: Sequence[Submission],
        ctx: ImportContext,
    ) -> None:
        """Best-effort avatar warm-up; failures here must not abort the import."""
        if ctx.no_avatars:
            return
        try:
            prefetch_avatars_for_submissions(submissions, ctx)
        except Exception as exc:
            ctx.log(
                f"Avatar prefetch failed (continuing without cache): {exc!s}",
                VerbosityLevel.DETAILED,
                "WARNING",
            )

    def _run_bulk_upserts(
        self,
        submissions: Sequence[Submission],
        ctx: ImportContext,
    ) -> None:
        """
        Pre-create rooms and upsert speakers in bulk.

        Skipped under ``--dry-run`` and ``--detect-only``: both modes must leave the
        Speaker/Room tables untouched. Also records which speakers had a visual change
        so the per-talk loop can decide on image regeneration.
        """
        if ctx.dry_run or ctx.detect_only:
            self._speakers_with_visual_change = frozenset()
            return
        batch_create_rooms(submissions, ctx)
        self._speakers_with_visual_change = frozenset(
            batch_create_or_update_speakers(submissions, ctx),
        )

    def _process_one_with_stats(
        self,
        submission: Submission,
        ctx: ImportContext,
        stats: dict[_StatKey, int],
    ) -> None:
        """Run a single submission through the pipeline and update *stats* in place."""
        try:
            if not is_valid_submission(submission, ctx):
                stats["skipped"] += 1
                return
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
        # Scope the match to the event being imported: pretalx_link already embeds the event
        # slug so a cross-event collision is implausible, but matching within ctx.event_obj
        # keeps a stray duplicate link from one event ever resolving against another's talk.
        existing_talk = Talk.objects.filter(
            event=ctx.event_obj,
            pretalx_link=data.pretalx_link,
        ).first()

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
        """
        Delete *existing_talk* when its submission is no longer confirmed/accepted.

        In ``--detect-only`` mode the delete is recorded as a pending change
        instead, so an admin can confirm it before the row disappears.
        """
        if not existing_talk:
            return "skipped"

        if ctx.detect_only:
            change, _ = record_pending_change(
                event=ctx.event_obj,
                pretalx_code=data.code,
                kind=PendingPretalxChange.Kind.DELETE,
                talk=existing_talk,
                field_diffs={},
                speaker_diffs={"added": [], "removed": []},
                pretalx_payload={"pretalx_link": data.pretalx_link, "title": data.title},
            )
            self._detected_changes.append(change)
            ctx.log(
                f"DETECT: would delete talk {data.title}",
                VerbosityLevel.DETAILED,
                "WARNING",
            )
            return "detected"

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
        """
        Sync *existing_talk* with fresh data.

        Returns ``"skipped"`` when ``--no-update`` is set, ``"unchanged"`` when the
        talk and speakers are already in sync with Pretalx, and ``"updated"``
        otherwise.

        Image regeneration is triggered when *any* of these is true (and
        ``--skip-images`` is not set): the talk data or speaker set changed,
        ``--force-images`` was passed, a still-attached speaker's name/avatar
        changed earlier in this run, or a social-card template is newer than the
        current image. The return status reflects the data diff only -
        force-regen does not promote ``"unchanged"`` to ``"updated"``.
        """
        if ctx.no_update:
            ctx.log(
                f"Skipping update for existing talk: {data.title}",
                VerbosityLevel.DETAILED,
                "WARNING",
            )
            return "skipped"

        if ctx.detect_only:
            return self._detect_update(existing_talk, data, speakers, ctx)

        changed = update_talk(existing_talk, data, speakers, ctx)
        if not changed:
            ctx.log(
                f"No changes for existing talk: {data.title}",
                VerbosityLevel.DETAILED,
            )

        if not ctx.dry_run and self._needs_image_regen(
            existing_talk,
            ctx,
            data_changed=changed,
        ):
            self._image_generator.generate(existing_talk, ctx)

        return "updated" if changed else "unchanged"

    def _detect_update(
        self,
        existing_talk: Talk,
        data: SubmissionData,
        speakers: list[SubmissionSpeaker],
        ctx: ImportContext,
    ) -> _ResultStatus:
        """
        Record a would-be UPDATE on *existing_talk* as a pending change.

        Computes the field and speaker diffs read-only, drops a
        ``PendingPretalxChange`` row when anything actually changed, and reports
        ``"unchanged"`` otherwise. Never touches the Talk.
        """
        field_diffs = serialize_field_diffs(existing_talk, data, ctx)
        speaker_diffs = diff_speakers(existing_talk, speakers)
        if not field_diffs and not speaker_diffs["added"] and not speaker_diffs["removed"]:
            ctx.log(
                f"No changes for existing talk: {data.title}",
                VerbosityLevel.DETAILED,
            )
            return "unchanged"

        change, _ = record_pending_change(
            event=ctx.event_obj,
            pretalx_code=data.code,
            kind=PendingPretalxChange.Kind.UPDATE,
            talk=existing_talk,
            field_diffs=field_diffs,
            speaker_diffs=speaker_diffs,
            pretalx_payload=build_submission_payload(data, speakers, ctx),
        )
        self._detected_changes.append(change)
        ctx.log(
            f"DETECT: pending UPDATE for talk {data.title}",
            VerbosityLevel.DETAILED,
            "WARNING",
        )
        return "detected"

    def _needs_image_regen(
        self,
        talk: Talk,
        ctx: ImportContext,
        *,
        data_changed: bool,
    ) -> bool:
        """
        Decide whether *talk*'s social-card image needs to be (re)built.

        ``--skip-images`` always wins. Otherwise regenerate when the talk data
        changed, ``--force-images`` is set, any of the talk's speakers had a
        name/avatar change in this run, or the current image is older than the
        latest social-card template PNG for this event.
        """
        if ctx.skip_images:
            return False
        if data_changed or ctx.force_images:
            return True
        if self._speakers_with_visual_change:
            talk_speaker_ids = set(talk.speakers.values_list("pretalx_id", flat=True))
            if talk_speaker_ids & self._speakers_with_visual_change:
                ctx.log(
                    f"Speaker name/avatar changed - regenerating image for: {talk.title}",
                    VerbosityLevel.DETAILED,
                )
                return True
        if self._template_mtime is not None and image_is_older_than(
            talk,
            self._template_mtime,
        ):
            ctx.log(
                f"Template is newer than image - regenerating image for: {talk.title}",
                VerbosityLevel.DETAILED,
            )
            return True
        return False

    def _handle_new(
        self,
        data: SubmissionData,
        speakers: list[SubmissionSpeaker],
        ctx: ImportContext,
    ) -> _ResultStatus:
        """
        Create a new :class:`~talks.models.Talk` and attach speakers + image.

        In ``--detect-only`` mode the would-be CREATE lands in a pending row
        instead, so an admin can decide whether to import it.
        """
        if ctx.detect_only:
            change, _ = record_pending_change(
                event=ctx.event_obj,
                pretalx_code=data.code,
                kind=PendingPretalxChange.Kind.CREATE,
                talk=None,
                field_diffs={},
                speaker_diffs=diff_speakers(None, speakers),
                pretalx_payload=build_submission_payload(data, speakers, ctx),
            )
            self._detected_changes.append(change)
            ctx.log(
                f"DETECT: pending CREATE for new talk {data.title}",
                VerbosityLevel.DETAILED,
                "WARNING",
            )
            return "detected"

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
        "unchanged": 0,
        "detected": 0,
        "deleted": 0,
        "skipped": 0,
        "failed": 0,
    }
