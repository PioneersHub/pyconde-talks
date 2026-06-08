"""
Minimal Pretalx REST API bindings: the response models the importer reads.

This module models only the fields the importer actually consumes and lets Pydantic drop everything
else (``extra="ignore"``), so a future API change to an unused field cannot break a sync. The HTTP
client that produces these objects lives in :mod:`~talks.management.commands._pretalx.client`.

Pretalx's versioned API (``v1``, June 2025) returns ``submission_type`` and ``track`` as expanded
sub-objects. The client always requests that expansion, so they are modelled as full objects, and
the handful of nested values the importer needs are exposed as navigation properties on
:class:`Submission`.
"""

# ``datetime`` is referenced in a Pydantic field annotation, which Pydantic resolves at runtime,
# so it must be a real import (not a type-checking-only one).
from datetime import datetime  # noqa: TC003
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class State(Enum):
    """Lifecycle state of a submission (mirrors the API's ``state`` field)."""

    submitted = "submitted"
    accepted = "accepted"
    rejected = "rejected"  # shown as "Not accepted" in the web UI
    confirmed = "confirmed"
    withdrawn = "withdrawn"
    canceled = "canceled"
    deleted = "deleted"


class MultiLingualStr(BaseModel):
    """
    A translated string such as ``{"en": "...", "de": "..."}``.

    The importer reads the English value via ``.en``; other languages are preserved
    (``extra="allow"``) but unused.
    """

    model_config = ConfigDict(extra="allow")

    en: str | None = None
    de: str | None = None


class SubmissionType(BaseModel):
    """Submission type as returned with ``expand=submission_type`` (id plus translated name)."""

    model_config = ConfigDict(extra="ignore")

    id: int
    name: MultiLingualStr


class Track(BaseModel):
    """Track as returned with ``expand=track`` (id plus translated name)."""

    model_config = ConfigDict(extra="ignore")

    id: int
    name: MultiLingualStr


class SlotRoom(BaseModel):
    """Room sub-object nested in a slot (``expand=slots.room``)."""

    model_config = ConfigDict(extra="ignore")

    id: int | None = None
    name: MultiLingualStr | None = None


class Slot(BaseModel):
    """A scheduled slot: when and in which room a submission is presented."""

    model_config = ConfigDict(extra="ignore")

    start: datetime | None = None
    room: SlotRoom | None = None


class SubmissionSpeaker(BaseModel):
    """A speaker as embedded in a submission (``expand=speakers``)."""

    model_config = ConfigDict(extra="ignore")

    code: str
    name: str
    biography: str | None = None
    avatar_url: str | None = None
    email: str | None = None


class Submission(BaseModel):
    """
    A Pretalx submission, trimmed to the fields the importer consumes.

    ``track`` and ``submission_type`` always arrive expanded (the client requests them via the
    ``expand`` query parameter), so they are modelled as full objects. The navigation properties
    expose the nested values the importer reads without making callers re-walk the tree.
    """

    model_config = ConfigDict(extra="ignore")

    code: str
    title: str
    state: State
    abstract: str | None = None
    description: str | None = None
    duration: int | None = None  # minutes
    image: str | None = None
    speakers: list[SubmissionSpeaker] = Field(default_factory=list)
    slots: list[Slot] | None = None
    track: Track | None = None
    submission_type: SubmissionType | None = None

    @property
    def first_slot(self) -> Slot | None:
        """The first scheduled slot, or ``None`` when the submission is unscheduled."""
        return self.slots[0] if self.slots else None

    @property
    def start(self) -> datetime | None:
        """Scheduled start of the first slot, if any."""
        slot = self.first_slot
        return slot.start if slot else None

    @property
    def room_name(self) -> str | None:
        """English name of the first slot's room, if a room is assigned."""
        slot = self.first_slot
        if slot is None or slot.room is None or slot.room.name is None:
            return None
        return slot.room.name.en

    @property
    def room_pretalx_id(self) -> int | None:
        """Stable Pretalx id of the first slot's room, if a room is assigned."""
        slot = self.first_slot
        return slot.room.id if slot and slot.room else None

    @property
    def track_name(self) -> str | None:
        """English name of the track, if any."""
        return self.track.name.en if self.track else None

    @property
    def submission_type_name(self) -> str | None:
        """English name of the submission type, if any."""
        return self.submission_type.name.en if self.submission_type else None


class Event(BaseModel):
    """Event metadata; the importer only reads ``name.en``."""

    model_config = ConfigDict(extra="ignore")

    name: MultiLingualStr
    slug: str
