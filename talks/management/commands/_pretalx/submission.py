"""
Extract clean data from a Pretalx ``Submission`` and classify submissions.

:class:`SubmissionData` normalizes a Pretalx submission into flat, truncated, safe-to-persist
fields. The classification helpers detect lightning talks and announcements so those speaker-less
submissions can still be imported.
"""

from datetime import timedelta
from typing import TYPE_CHECKING

from talks.models import (
    FAR_FUTURE,
    MAX_ROOM_NAME_LENGTH,
    MAX_TALK_TITLE_LENGTH,
    MAX_TRACK_NAME_LENGTH,
)


if TYPE_CHECKING:
    from talks.management.commands._pretalx.pretalx_models import Submission


class SubmissionData:
    """
    Flat, truncated representation of a Pretalx :class:`Submission`.

    The submission's navigation properties handle the null-safe digging through the nested API
    shape; this class only applies importer policy: capping each string to its Django model
    max-length, building the talk link, and defaulting an unscheduled talk to ``FAR_FUTURE``.
    """

    def __init__(self, submission: Submission, pretalx_event_url: str) -> None:
        """Extract and normalize fields from *submission*."""
        self.code = submission.code
        self.title = submission.title[:MAX_TALK_TITLE_LENGTH] if submission.title else ""
        self.abstract = submission.abstract or ""
        self.description = submission.description or ""
        self.pretalx_link = f"{pretalx_event_url.rstrip('/')}/talk/{submission.code}"
        self.image_url = submission.image or ""
        self.room = (submission.room_name or "")[:MAX_ROOM_NAME_LENGTH]
        self.pretalx_room_id = submission.room_pretalx_id
        self.track = (submission.track_name or "")[:MAX_TRACK_NAME_LENGTH]
        self.start_time = submission.start or FAR_FUTURE
        self.duration = timedelta(minutes=submission.duration) if submission.duration else None
        self.submission_type = submission.submission_type_name or ""


# ------------------------------------------------------------------
# Submission classification helpers
# ------------------------------------------------------------------

_LIGHTNING_TERMS = frozenset(
    {
        "lightning",
        "lightning talk",
        "lightning talks",
        "lightning talks (1/2)",
        "lightning talks (2/2)",
    },
)

_ANNOUNCEMENT_TERMS = frozenset(
    {
        "opening session",
        "closing session",
    },
)


def _matches_terms(submission: Submission, terms: frozenset[str]) -> bool:
    """Return ``True`` if the submission's title, type, or track name matches *terms*."""
    candidates = (submission.title, submission.submission_type_name, submission.track_name)
    return any(value is not None and value.lower() in terms for value in candidates)


def submission_is_lightning_talk(submission: Submission) -> bool:
    """Check if *submission* is a lightning talk."""
    return _matches_terms(submission, _LIGHTNING_TERMS)


def submission_is_announcement(submission: Submission) -> bool:
    """Check if *submission* is an announcement (opening / closing session)."""
    return _matches_terms(submission, _ANNOUNCEMENT_TERMS)
