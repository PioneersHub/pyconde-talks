"""Extract clean data from a Pretalx Submission and classify submissions."""

from datetime import datetime, timedelta
from typing import TYPE_CHECKING, cast

from django.conf import settings

from talks.models import (
    FAR_FUTURE,
    MAX_ROOM_NAME_LENGTH,
    MAX_TALK_TITLE_LENGTH,
    MAX_TRACK_NAME_LENGTH,
)


if TYPE_CHECKING:
    from pytanis.pretalx.models import Submission


class SubmissionData:
    """Extract and hold clean data from a Pretalx :class:`Submission`."""

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

        self.room = self._extract_room(submission)
        self.track = self._extract_track(submission)
        self.start_time = self._extract_start_time(submission)
        self.duration = self._extract_duration(submission)
        self.submission_type = self._extract_submission_type(submission)

    # ------------------------------------------------------------------
    # Private extraction helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_room(submission: Submission) -> str:
        if (
            hasattr(submission, "slots")
            and submission.slots
            and hasattr(submission.slots[0], "room")
            and submission.slots[0].room
            and hasattr(submission.slots[0].room, "name")
            and submission.slots[0].room.name
            and "en" in submission.slots[0].room.name
        ):
            return str(submission.slots[0].room.name["en"])[:MAX_ROOM_NAME_LENGTH]
        return ""

    @staticmethod
    def _extract_track(submission: Submission) -> str:
        if (
            hasattr(submission, "track")
            and submission.track
            and hasattr(submission.track, "name")
            and submission.track.name
            and hasattr(submission.track.name, "en")
            and submission.track.name.en
        ):
            return submission.track.name.en[:MAX_TRACK_NAME_LENGTH]
        return ""

    @staticmethod
    def _extract_start_time(submission: Submission) -> datetime:
        if (
            hasattr(submission, "slots")
            and submission.slots
            and hasattr(submission.slots[0], "start")
            and submission.slots[0].start
        ):
            return submission.slots[0].start
        return FAR_FUTURE

    @staticmethod
    def _extract_duration(submission: Submission) -> timedelta | None:
        if hasattr(submission, "duration") and submission.duration:
            return timedelta(minutes=submission.duration)
        return None

    @staticmethod
    def _extract_submission_type(submission: Submission) -> str:
        if (
            hasattr(submission, "submission_type")
            and submission.submission_type
            and hasattr(submission.submission_type, "en")
            and submission.submission_type.en
        ):
            return str(submission.submission_type.en)
        return ""


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
    """Return ``True`` if any relevant field of *submission* matches *terms*."""
    fields = [
        getattr(submission, "track", None),
        getattr(submission, "title", None),
        getattr(submission, "submission_type", None),
    ]
    for field in fields:
        if isinstance(field, str) and field.lower() in terms:
            return True
        if (
            field is not None
            and hasattr(field, "en")
            and isinstance(field.en, str)
            and field.en.lower() in terms
        ):
            return True
    return False


def submission_is_lightning_talk(submission: Submission) -> bool:
    """Check if *submission* is a lightning talk."""
    return _matches_terms(submission, _LIGHTNING_TERMS)


def submission_is_announcement(submission: Submission) -> bool:
    """Check if *submission* is an announcement (opening / closing session)."""
    return _matches_terms(submission, _ANNOUNCEMENT_TERMS)
