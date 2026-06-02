"""
Unit tests for small, easily-reachable helpers in ``talks.management.commands._pretalx``.

These cover the argument plumbing, single-record helpers, and warning paths that don't need a live
Pretalx API or mocked HTTP layer. The bigger integration flows still live in
``test_import_pretalx_command.py`` and ``test_import_pretalx_integration.py``.
"""
# ruff: noqa: PLR2004

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import MagicMock

import pytest
from model_bakery import baker
from pytanis.pretalx.models import State

from events.models import Event
from talks.management.commands._pretalx.context import ImportContext
from talks.management.commands._pretalx.events import (
    get_or_create_event,
    maybe_update_event_name,
    resolve_event_slug,
    resolve_pretalx_url,
    split_pretalx_url,
)
from talks.management.commands._pretalx.rooms import get_or_create_room
from talks.management.commands._pretalx.speakers import (
    batch_create_or_update_speakers,
    get_or_create_speaker,
    maybe_update_speaker,
)
from talks.management.commands._pretalx.submission import SubmissionData
from talks.management.commands._pretalx.talks import (
    add_speakers_to_talk,
    create_talk,
    update_talk,
    update_talk_speakers,
)
from talks.management.commands._pretalx.types import LogFn, VerbosityLevel
from talks.management.commands._pretalx.validation import is_valid_submission
from talks.models import MAX_TALK_TITLE_LENGTH, Room, Speaker, Talk


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _capture_log() -> tuple[LogFn, list[tuple[str, str | None]]]:
    """Return a log function plus the list it appends to."""
    entries: list[tuple[str, str | None]] = []

    def log_fn(
        message: str,
        verbosity: VerbosityLevel,
        min_level: VerbosityLevel,
        style: str | None = None,
    ) -> None:
        entries.append((message, style))

    return log_fn, entries


def _noop_log(
    message: str,
    verbosity: VerbosityLevel,
    min_level: VerbosityLevel,
    style: str | None = None,
) -> None:
    """Silent log function for tests that don't need logging output."""


def _ctx(
    log_fn: LogFn | None = None,
    **overrides: Any,
) -> ImportContext:
    fn: LogFn = log_fn if log_fn is not None else _noop_log
    return ImportContext(verbosity=VerbosityLevel.TRACE, log_fn=fn, **overrides)


def _mock_submission_speaker(
    code: str = "SPK-1",
    name: str = "Ada Lovelace",
    biography: str = "",
    avatar_url: str = "",
) -> MagicMock:
    sp = MagicMock()
    sp.code = code
    sp.name = name
    sp.biography = biography
    sp.avatar_url = avatar_url
    return sp


def _mock_submission(
    *,
    code: str = "SUB-1",
    title: str = "A Talk",
    submission_type: str = "Talk",
    track: str | None = "PyData",
    room: str = "Main Hall",
    duration: int = 30,
    start: datetime | None = None,
    state: State = State.confirmed,
    speakers: list[MagicMock] | None = None,
    image: str = "",
) -> MagicMock:
    sub = MagicMock()
    sub.code = code
    sub.title = title
    sub.abstract = "abs"
    sub.description = "desc"
    sub.state = state
    sub.duration = duration
    sub.image = image

    slot = MagicMock()
    if room:
        slot.room.name = {"en": room}
    else:
        slot.room = None
    slot.start = start or datetime(2099, 6, 1, 10, 0, tzinfo=UTC)
    sub.slots = [slot] if slot else []

    if track is None:
        sub.track = None
    else:
        sub.track = MagicMock()
        sub.track.name = MagicMock()
        sub.track.name.en = track

    sub.submission_type = MagicMock()
    sub.submission_type.en = submission_type

    sub.speakers = speakers if speakers is not None else [_mock_submission_speaker()]
    return sub


# ---------------------------------------------------------------------------
# ImportContext
# ---------------------------------------------------------------------------
class TestImportContext:
    """ImportContext is a frozen dataclass with an ``evolve`` helper."""

    def test_frozen(self) -> None:
        """Direct attribute assignment is blocked on the frozen dataclass."""
        ctx = _ctx()
        with pytest.raises(FrozenInstanceError):
            ctx.dry_run = True  # type: ignore[misc]

    def test_evolve_returns_updated_copy(self) -> None:
        """``evolve`` produces a new instance with the requested field overridden."""
        ctx = _ctx(dry_run=False)
        evolved = ctx.evolve(dry_run=True)
        assert ctx.dry_run is False
        assert evolved.dry_run is True
        assert evolved.log_fn is ctx.log_fn

    def test_from_options_maps_defaults(self) -> None:
        """``from_options`` uses sensible defaults for missing keys."""
        log_fn, _ = _capture_log()
        ctx = ImportContext.from_options(
            {"verbosity": 1},
            log_fn=log_fn,
        )
        assert ctx.verbosity == VerbosityLevel.NORMAL
        assert ctx.dry_run is False
        assert ctx.no_update is False
        assert ctx.image_format == "webp"
        assert ctx.max_retries == 3

    def test_from_options_reads_flags(self) -> None:
        """All CLI flags are threaded through to the ImportContext."""
        log_fn, _ = _capture_log()
        ctx = ImportContext.from_options(
            {
                "verbosity": 2,
                "dry_run": True,
                "no_update": True,
                "skip_images": True,
                "no_avatars": True,
                "image_format": "jpeg",
                "max_retries": 5,
                "pretalx_event_url": "https://pretalx.com/slug",
                "event_slug": "slug",
                "event_name": "Test",
                "api_token": "t",
            },
            log_fn=log_fn,
        )
        assert ctx.verbosity == VerbosityLevel.DETAILED
        assert ctx.dry_run is True
        assert ctx.no_update is True
        assert ctx.skip_images is True
        assert ctx.no_avatars is True
        assert ctx.image_format == "jpeg"
        assert ctx.max_retries == 5
        assert ctx.pretalx_event_url == "https://pretalx.com/slug"
        assert ctx.event_slug == "slug"
        assert ctx.event_name == "Test"
        assert ctx.api_token == "t"


# ---------------------------------------------------------------------------
# events.py
# ---------------------------------------------------------------------------
@pytest.mark.django_db
class TestEventHelpers:
    """Helpers that translate CLI args + API data into Django Event rows."""

    def test_resolve_event_slug_from_cli_flag(self) -> None:
        """An explicit --event-slug wins over the Pretalx URL."""
        assert resolve_event_slug(_ctx(event_slug="pyconde-2099")) == "pyconde-2099"

    def test_resolve_event_slug_derives_from_url(self) -> None:
        """Falling back to the last URL segment and logging a warning."""
        log_fn, entries = _capture_log()
        ctx = _ctx(log_fn=log_fn, pretalx_event_url="https://pretalx.com/pyconde-2099/")
        assert resolve_event_slug(ctx) == "pyconde-2099"
        # A warning is emitted so the operator notices the fallback.
        assert any(style == "WARNING" for _, style in entries)

    def test_resolve_event_slug_missing_both_returns_none(self) -> None:
        """With neither slug nor URL, return None and log an error."""
        log_fn, entries = _capture_log()
        ctx = _ctx(log_fn=log_fn)
        assert resolve_event_slug(ctx) is None
        assert any(style == "ERROR" for _, style in entries)

    def test_get_or_create_event_creates_when_missing(self) -> None:
        """New slugs create Events using the CLI-provided name and URL."""
        ctx = _ctx(event_name="PyConDE 2099", pretalx_event_url="https://pretalx.com/pyconde-2099")
        event, created = get_or_create_event("pyconde-2099", ctx)
        assert created is True
        assert event.name == "PyConDE 2099"
        assert event.pretalx_url == "https://pretalx.com/pyconde-2099"

    def test_get_or_create_event_returns_existing(self) -> None:
        """Existing slugs are returned as-is with ``created=False``."""
        Event.objects.create(slug="existing", name="Existing", year=2099)
        event, created = get_or_create_event("existing", _ctx())
        assert created is False
        assert event.slug == "existing"

    def test_resolve_pretalx_url_precedence(self) -> None:
        """CLI URL > event.pretalx_url > default pretalx.com."""
        event = Event(slug="s", name="s", year=2099, pretalx_url="https://ev.example/s")
        assert resolve_pretalx_url("https://cli/s", event, "s") == "https://cli/s"
        assert resolve_pretalx_url("", event, "s") == "https://ev.example/s"
        event.pretalx_url = ""
        assert resolve_pretalx_url("", event, "s") == "https://pretalx.com/s"

    def test_split_pretalx_url(self) -> None:
        """Trailing slashes and path segments are split consistently."""
        assert split_pretalx_url("https://pretalx.com/pyconde/") == (
            "https://pretalx.com",
            "pyconde",
        )

    def test_maybe_update_event_name_updates_when_created(self) -> None:
        """A freshly-created Event with a generic slug adopts the API event name."""
        event = Event.objects.create(slug="pyconde-2099", name="pyconde-2099", year=2099)
        client = MagicMock()
        api_event = MagicMock()
        api_event.name.en = "PyConDE / PyData 2099"
        client.event.return_value = api_event

        name = maybe_update_event_name(client, "pyconde-2099", event, _ctx(), created=True)
        event.refresh_from_db()
        assert name == "PyConDE / PyData 2099"
        assert event.name == "PyConDE / PyData 2099"

    def test_maybe_update_event_name_no_change_when_existing(self) -> None:
        """Already-existing Events keep their stored name even with an API response."""
        event = Event.objects.create(slug="pyconde-2099", name="Human Name", year=2099)
        client = MagicMock()
        api_event = MagicMock()
        api_event.name.en = "Some Other Name"
        client.event.return_value = api_event

        maybe_update_event_name(client, "pyconde-2099", event, _ctx(), created=False)
        event.refresh_from_db()
        assert event.name == "Human Name"


# ---------------------------------------------------------------------------
# rooms.py - get_or_create_room single helper
# ---------------------------------------------------------------------------
@pytest.mark.django_db
class TestGetOrCreateRoom:
    """Single-room helper used when processing a submission."""

    def test_empty_name_returns_none(self) -> None:
        """No room name means no room object is returned."""
        assert get_or_create_room("", _ctx()) is None

    def test_returns_existing(self) -> None:
        """Existing rooms are reused rather than duplicated."""
        existing = Room.objects.create(name="Main Hall")
        result = get_or_create_room("Main Hall", _ctx())
        assert result == existing
        assert Room.objects.count() == 1

    def test_creates_new(self) -> None:
        """Unknown names are persisted as new Rooms."""
        result = get_or_create_room("New Room", _ctx())
        assert result is not None
        assert Room.objects.filter(name="New Room").exists()

    def test_dry_run_returns_unsaved(self) -> None:
        """Dry-run returns an unsaved Room so callers can still reference it."""
        result = get_or_create_room("Dry Room", _ctx(dry_run=True))
        assert result is not None
        assert result.pk is None
        assert not Room.objects.filter(name="Dry Room").exists()

    def test_detect_only_returns_unsaved(self) -> None:
        """Detect-only must not write a Room: building a diff stays read-only."""
        result = get_or_create_room("Detect Room", _ctx(detect_only=True))
        assert result is not None
        assert result.pk is None
        assert not Room.objects.filter(name="Detect Room").exists()

    def test_creates_scoped_to_event_with_id(self) -> None:
        """A brand-new room is created under the context event with its Pretalx id."""
        event = Event.objects.create(slug="e", name="E", year=2099)
        room = get_or_create_room("Hall", _ctx(event_obj=event), pretalx_id=4993)
        assert room is not None
        assert room.event == event
        assert room.pretalx_id == 4993

    def test_matches_by_id_renames_in_place(self) -> None:
        """A room renamed on Pretalx keeps the same row; only its name changes."""
        event = Event.objects.create(slug="e", name="E", year=2099)
        original = Room.objects.create(event=event, name="Old Name", pretalx_id=4993)
        result = get_or_create_room("New Name", _ctx(event_obj=event), pretalx_id=4993)
        assert result is not None
        assert result.pk == original.pk  # same row, not a duplicate
        original.refresh_from_db()
        assert original.name == "New Name"
        assert Room.objects.filter(event=event).count() == 1

    def test_lazy_stamps_pretalx_id_on_legacy_room(self) -> None:
        """A legacy room matched by name gets its pretalx_id stamped on first sync."""
        event = Event.objects.create(slug="e", name="E", year=2099)
        legacy = Room.objects.create(event=event, name="Hall", pretalx_id=None)
        get_or_create_room("Hall", _ctx(event_obj=event), pretalx_id=4993)
        legacy.refresh_from_db()
        assert legacy.pretalx_id == 4993

    def test_detect_only_does_not_rename_or_stamp(self) -> None:
        """Detect-only resolves the room but never renames or stamps it."""
        event = Event.objects.create(slug="e", name="E", year=2099)
        room = Room.objects.create(event=event, name="Old Name", pretalx_id=4993)
        result = get_or_create_room(
            "New Name",
            _ctx(detect_only=True, event_obj=event),
            pretalx_id=4993,
        )
        # Returned the existing row, but neither memory nor DB was mutated.
        assert result is not None
        assert result.pk == room.pk
        room.refresh_from_db()
        assert room.name == "Old Name"


# ---------------------------------------------------------------------------
# speakers.py single/internal helpers
# ---------------------------------------------------------------------------
@pytest.mark.django_db
class TestSpeakerHelpers:
    """Single-speaker helper and internal partition/diff logic."""

    def test_get_or_create_speaker_creates_new(self) -> None:
        """Unknown codes create a persisted Speaker."""
        sp = _mock_submission_speaker(
            code="X1",
            name="Grace Hopper",
            biography="...",
            avatar_url="",
        )
        speaker = get_or_create_speaker(sp, _ctx())
        assert speaker.pretalx_id == "X1"
        assert Speaker.objects.filter(pretalx_id="X1").exists()

    def test_get_or_create_speaker_updates_existing(self) -> None:
        """Existing speakers get fields refreshed when they differ."""
        Speaker.objects.create(
            name="Old Name",
            biography="old bio",
            avatar="",
            pretalx_id="X1",
        )
        sp = _mock_submission_speaker(code="X1", name="New Name", biography="new bio")
        get_or_create_speaker(sp, _ctx())
        refreshed = Speaker.objects.get(pretalx_id="X1")
        assert refreshed.name == "New Name"
        assert refreshed.biography == "new bio"

    def test_dry_run_returns_unsaved_speaker(self) -> None:
        """Dry-run returns an unsaved instance without hitting the DB."""
        sp = _mock_submission_speaker(code="NEW", name="Dry Spk")
        speaker = get_or_create_speaker(sp, _ctx(dry_run=True))
        assert speaker.pk is None
        assert not Speaker.objects.filter(pretalx_id="NEW").exists()

    def test_maybe_update_speaker_no_update_flag(self) -> None:
        """--no-update preserves existing speaker data."""
        existing = Speaker.objects.create(
            name="Keep",
            biography="keep",
            avatar="",
            pretalx_id="K1",
        )
        maybe_update_speaker(
            existing,
            _mock_submission_speaker(code="K1", name="Ignored"),
            _ctx(no_update=True),
        )
        existing.refresh_from_db()
        assert existing.name == "Keep"

    def test_maybe_update_speaker_dry_run(self) -> None:
        """Dry-run short-circuits before any DB write."""
        existing = Speaker.objects.create(
            name="Keep",
            biography="keep",
            avatar="",
            pretalx_id="K1",
        )
        maybe_update_speaker(
            existing,
            _mock_submission_speaker(code="K1", name="Different"),
            _ctx(dry_run=True),
        )
        existing.refresh_from_db()
        assert existing.name == "Keep"

    def test_maybe_update_speaker_noop_when_unchanged(self) -> None:
        """Unchanged speakers never get a spurious UPDATE."""
        existing = Speaker.objects.create(
            name="Same",
            biography="same",
            avatar="https://avatar",
            pretalx_id="S1",
        )
        log_fn, entries = _capture_log()
        maybe_update_speaker(
            existing,
            _mock_submission_speaker(
                code="S1",
                name="Same",
                biography="same",
                avatar_url="https://avatar",
            ),
            _ctx(log_fn=log_fn),
        )
        assert not any("Updated speaker" in m for m, _ in entries)

    def test_batch_create_or_update_speakers_empty_submissions(self) -> None:
        """No submissions means no DB work and no log output."""
        log_fn, entries = _capture_log()
        batch_create_or_update_speakers([], _ctx(log_fn=log_fn))
        assert entries == []
        assert Speaker.objects.count() == 0

    def test_batch_create_or_update_speakers_upserts_changes(self) -> None:
        """Default (no --no-update) path overwrites existing speakers with submission data."""
        Speaker.objects.create(name="Old", biography="old bio", avatar="", pretalx_id="SPK-1")
        batch_create_or_update_speakers([_mock_submission()], _ctx())
        refreshed = Speaker.objects.get(pretalx_id="SPK-1")
        # The default mocked submission's first speaker overrides the existing row.
        assert refreshed.name != "Old"

    def test_batch_create_or_update_speakers_no_update_skips_changes(self) -> None:
        """--no-update creates new speakers but leaves existing rows untouched."""
        Speaker.objects.create(name="Keep", biography="keep bio", avatar="", pretalx_id="SPK-1")
        batch_create_or_update_speakers([_mock_submission()], _ctx(no_update=True))
        refreshed = Speaker.objects.get(pretalx_id="SPK-1")
        assert refreshed.name == "Keep"
        assert refreshed.biography == "keep bio"


# ---------------------------------------------------------------------------
# talks.py - create_talk / update_talk / add_speakers_to_talk / update_talk_speakers
# ---------------------------------------------------------------------------
@pytest.mark.django_db
class TestCreateAndUpdateTalk:
    """Higher-level helpers that actually write Talk rows."""

    def _data(self, **kwargs: Any) -> SubmissionData:
        sub = _mock_submission(**kwargs)
        return SubmissionData(sub, "https://pretalx.com/pyconde-2099")

    def test_create_talk_persists_all_fields(self) -> None:
        """create_talk writes a row tied to the context's event, with every mapped field set."""
        event = Event.objects.create(slug="x", name="X", year=2099)
        ctx = _ctx(event_obj=event, pretalx_event_url="https://pretalx.com/x")
        data = self._data(code="AA1", title="Create Test", room="Alpha")
        talk = create_talk(data, ctx)
        assert talk.pk is not None
        assert talk.event == event
        assert talk.title == "Create Test"
        assert talk.room is not None
        assert talk.room.name == "Alpha"

    def test_update_talk_syncs_fields_and_event(self) -> None:
        """update_talk overwrites fields and moves the Talk to the new context event."""
        old_event = Event.objects.create(slug="old", name="O", year=2098)
        new_event = Event.objects.create(slug="new", name="N", year=2099)
        room = Room.objects.create(name="OldRoom")
        talk = baker.make(
            Talk,
            title="Old",
            event=old_event,
            room=room,
            duration=timedelta(minutes=30),
        )
        data = self._data(code="AA2", title="New Title", room="NewRoom", duration=60)
        ctx = _ctx(event_obj=new_event, pretalx_event_url="https://pretalx.com/new")

        changed = update_talk(talk, data, [], ctx)

        assert changed is True
        talk.refresh_from_db()
        assert talk.title == "New Title"
        assert talk.event == new_event
        assert talk.room is not None
        assert talk.room.name == "NewRoom"

    def test_update_talk_returns_false_when_already_in_sync(self) -> None:
        """A re-run of the import on a Talk that matches Pretalx is a no-op."""
        event = Event.objects.create(slug="sync", name="Sync", year=2099)
        Room.objects.get_or_create(name="Main Hall")
        pretalx_url = "https://pretalx.com/sync"
        sub = _mock_submission(code="UNCHANGED")
        data = SubmissionData(sub, pretalx_url)

        # Create the Talk through the same code path so all defaults line up.
        ctx = _ctx(event_obj=event, pretalx_event_url=pretalx_url)
        talk = create_talk(data, ctx)
        pre_updated_at = talk.updated_at

        changed = update_talk(talk, data, [], ctx)

        assert changed is False
        talk.refresh_from_db()
        # ``updated_at`` only bumps when ``save()`` is actually called.
        assert talk.updated_at == pre_updated_at

    def test_update_talk_returns_true_when_speakers_change(self) -> None:
        """A speaker swap counts as a change even if every Talk field already matches."""
        event = Event.objects.create(slug="spk", name="Spk", year=2099)
        pretalx_url = "https://pretalx.com/spk"
        sub = _mock_submission(code="AA3")
        data = SubmissionData(sub, pretalx_url)
        ctx = _ctx(event_obj=event, pretalx_event_url=pretalx_url)
        talk = create_talk(data, ctx)

        new_speaker = _mock_submission_speaker(code="NEW", name="New Person")
        changed = update_talk(talk, data, [new_speaker], ctx)

        assert changed is True
        assert "NEW" in set(talk.speakers.values_list("pretalx_id", flat=True))

    def test_add_speakers_to_talk_creates_and_links(self) -> None:
        """Speakers listed on the submission are added to the Talk's m2m set."""
        event = Event.objects.create(slug="x2", name="X2", year=2099)
        talk = baker.make(Talk, event=event)
        speakers = [
            _mock_submission_speaker(code="S1", name="Alice"),
            _mock_submission_speaker(code="S2", name="Bob"),
        ]
        add_speakers_to_talk(talk, speakers, _ctx())  # type: ignore[arg-type]
        assert set(talk.speakers.values_list("pretalx_id", flat=True)) == {"S1", "S2"}

    def test_add_speakers_empty_list_is_noop(self) -> None:
        """An empty speaker list does not log a success line."""
        talk = baker.make(Talk)
        log_fn, entries = _capture_log()
        add_speakers_to_talk(talk, [], _ctx(log_fn=log_fn))
        assert entries == []
        assert talk.speakers.count() == 0

    def test_update_talk_speakers_dry_run_is_noop(self) -> None:
        """Dry-run does not mutate the speaker set."""
        talk = baker.make(Talk)
        sp = Speaker.objects.create(name="Keep", biography="", avatar="", pretalx_id="K")
        talk.speakers.add(sp)
        update_talk_speakers(
            talk,
            [_mock_submission_speaker(code="NEW", name="Drop In")],
            _ctx(dry_run=True),
        )
        assert list(talk.speakers.values_list("pretalx_id", flat=True)) == ["K"]

    def test_update_talk_speakers_no_update_is_noop(self) -> None:
        """--no-update is honored for speaker sync."""
        talk = baker.make(Talk)
        sp = Speaker.objects.create(name="Keep", biography="", avatar="", pretalx_id="K")
        talk.speakers.add(sp)
        update_talk_speakers(
            talk,
            [_mock_submission_speaker(code="NEW", name="Drop In")],
            _ctx(no_update=True),
        )
        assert list(talk.speakers.values_list("pretalx_id", flat=True)) == ["K"]

    def test_update_talk_persists_image_url(self) -> None:
        """When the submission has an image, it overwrites ``external_image_url``."""
        event = Event.objects.create(slug="img", name="IMG", year=2099)
        talk = baker.make(Talk, event=event, external_image_url="https://old/img.jpg")
        data = self._data(image="https://pretalx/new.jpg")
        update_talk(talk, data, [], _ctx(event_obj=event))
        talk.refresh_from_db()
        assert talk.external_image_url == "https://pretalx/new.jpg"

    def test_submission_data_empty_type_returns_empty_string(self) -> None:
        """Missing ``submission_type.en`` extracts cleanly as an empty string."""
        sub = _mock_submission()
        sub.submission_type = None
        data = SubmissionData(sub, "https://pretalx.com/x")
        assert data.submission_type == ""

    def test_update_talk_speakers_adds_and_removes(self) -> None:
        """The new set replaces the old: additions happen, stale speakers are unlinked."""
        talk = baker.make(Talk)
        keeper = Speaker.objects.create(name="Keep", biography="", avatar="", pretalx_id="K")
        stale = Speaker.objects.create(name="Stale", biography="", avatar="", pretalx_id="S")
        talk.speakers.add(keeper, stale)
        update_talk_speakers(
            talk,
            [
                _mock_submission_speaker(code="K", name="Keep"),
                _mock_submission_speaker(code="N", name="Newcomer"),
            ],
            _ctx(),
        )
        assert set(talk.speakers.values_list("pretalx_id", flat=True)) == {"K", "N"}


# ---------------------------------------------------------------------------
# validation.py - long title + missing room warnings
# ---------------------------------------------------------------------------
@pytest.mark.django_db
class TestValidation:
    """Extra warning paths in ``is_valid_submission``."""

    def test_long_title_is_accepted_with_warning(self) -> None:
        """Long titles are valid but produce a WARNING log entry."""
        log_fn, entries = _capture_log()
        submission = _mock_submission(title="x" * (MAX_TALK_TITLE_LENGTH + 1))
        assert is_valid_submission(submission, _ctx(log_fn=log_fn)) is True
        assert any("too long" in m.lower() for m, _ in entries)

    def test_missing_room_warning_at_trace(self) -> None:
        """Rooms default to None with a TRACE warning, but the submission is still valid."""
        log_fn, entries = _capture_log()
        submission = _mock_submission(room="")
        submission.slots = []
        assert is_valid_submission(submission, _ctx(log_fn=log_fn)) is True
        assert any("no room" in m.lower() for m, _ in entries)
