"""
Submission validation for the Pretalx importer.

Determines whether a submission should be imported based on title presence, speaker presence (with
exceptions for lightning talks / announcements), and optional room assignment checks.
"""

from typing import TYPE_CHECKING

from django.conf import settings

from talks.management.commands._pretalx.submission import (
    submission_is_announcement,
    submission_is_lightning_talk,
)
from talks.management.commands._pretalx.types import VerbosityLevel
from talks.models import MAX_TALK_TITLE_LENGTH


if TYPE_CHECKING:
    from pytanis.pretalx.models import Submission

    from talks.management.commands._pretalx.context import ImportContext


def is_valid_submission(
    submission: Submission,
    ctx: ImportContext,
) -> bool:
    """
    Return ``True`` if *submission* passes all validation checks.

    Runs title, speaker, and room checks; logs warnings for non-fatal
    issues (long titles, missing rooms).
    """
    valid = _validate_title(submission, ctx)
    _warn_long_title(submission, ctx)
    valid = _validate_speakers(submission, ctx) and valid
    _warn_missing_room(submission, ctx)
    return valid


def _validate_title(
    submission: Submission,
    ctx: ImportContext,
) -> bool:
    """Return ``False`` if the submission has no title."""
    if not submission.title:
        ctx.log(
            f"Submission {submission.code} has no title",
            VerbosityLevel.NORMAL,
            "ERROR",
        )
        return False
    return True


def _warn_long_title(
    submission: Submission,
    ctx: ImportContext,
) -> None:
    """Log a warning if the title exceeds the maximum length."""
    if submission.title and len(submission.title) > MAX_TALK_TITLE_LENGTH:
        ctx.log(
            f"Submission title too long, will be truncated: {submission.title}",
            VerbosityLevel.NORMAL,
            "WARNING",
        )


def _validate_speakers(
    submission: Submission,
    ctx: ImportContext,
) -> bool:
    """Validate speaker presence, allowing exceptions for lightning talks and announcements."""
    if submission.speakers:
        return True

    is_special = submission_is_lightning_talk(submission) or submission_is_announcement(submission)
    valid: bool = True if is_special else settings.IMPORT_TALKS_WITHOUT_SPEAKERS

    ctx.log(
        f"Submission {submission.code} has no speakers",
        VerbosityLevel.NORMAL,
        "WARNING",
    )
    return valid


def _warn_missing_room(
    submission: Submission,
    ctx: ImportContext,
) -> None:
    """Log a trace-level warning if the submission has no room assigned."""
    has_room = (
        hasattr(submission, "slots")
        and submission.slots
        and hasattr(submission.slots[0], "room")
        and submission.slots[0].room
    )
    if not has_room:
        ctx.log(
            f"Submission {submission.code} has no room assigned",
            VerbosityLevel.TRACE,
            "WARNING",
        )
