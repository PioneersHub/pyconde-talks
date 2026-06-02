"""Tests for the post-detect email digest helper."""

import pytest
from django.core import mail
from django.test import override_settings

from events.models import Event
from talks.management.commands._pretalx.context import ImportContext
from talks.management.commands._pretalx.digest import maybe_send_digest
from talks.management.commands._pretalx.types import VerbosityLevel
from talks.models import PendingPretalxChange


pytestmark = pytest.mark.django_db


def _ctx(event: Event | None = None) -> ImportContext:
    """Return a minimal context for digest tests."""

    def _noop(*_args: object, **_kwargs: object) -> None:
        """Silent log function."""

    return ImportContext(verbosity=VerbosityLevel.NORMAL, log_fn=_noop, event_obj=event)


def _make_pending(event: Event, code: str, title: str) -> PendingPretalxChange:
    """Create a pending CREATE row with a payload title for the digest body."""
    return PendingPretalxChange.objects.create(
        event=event,
        pretalx_code=code,
        kind=PendingPretalxChange.Kind.CREATE,
        pretalx_payload={"title": title},
    )


class TestDigest:
    """``maybe_send_digest`` only sends when there is something to report."""

    def test_empty_list_sends_nothing(self) -> None:
        """An empty changes list short-circuits without ever calling send_mail."""
        sent = maybe_send_digest([], _ctx())
        assert sent is False
        assert mail.outbox == []

    @override_settings(
        PRETALX_DIGEST_RECIPIENTS=["ops@example.com"],
        DEFAULT_FROM_EMAIL="bot@example.com",
    )
    def test_sends_summary_when_changes_present(self) -> None:
        """A single email goes out with each change's summary line in the body."""
        event = Event.objects.create(slug="evt", name="Evt", year=2099)
        changes = [
            _make_pending(event, "AAA", "First New Talk"),
            _make_pending(event, "BBB", "Second New Talk"),
        ]

        sent = maybe_send_digest(changes, _ctx(event=event))

        assert sent is True
        assert len(mail.outbox) == 1
        msg = mail.outbox[0]
        assert msg.to == ["ops@example.com"]
        assert "evt" in msg.subject
        assert "2 Pretalx change(s) detected" in msg.subject
        assert "First New Talk" in msg.body
        assert "Second New Talk" in msg.body

    @override_settings(PRETALX_DIGEST_RECIPIENTS=[])
    def test_no_recipients_skips_send(self) -> None:
        """No recipients configured means no email is sent (no crash either)."""
        event = Event.objects.create(slug="evt", name="Evt", year=2099)
        changes = [_make_pending(event, "AAA", "T")]

        sent = maybe_send_digest(changes, _ctx(event=event))

        assert sent is False
        assert mail.outbox == []

    @override_settings(
        PRETALX_DIGEST_RECIPIENTS=None,
        ADMINS=[("Ops", "ops-fallback@example.com")],
        DEFAULT_FROM_EMAIL="bot@example.com",
    )
    def test_falls_back_to_admins_when_setting_unset(self) -> None:
        """Without an explicit recipient list, Django's ADMINS are used."""
        event = Event.objects.create(slug="evt", name="Evt", year=2099)
        changes = [_make_pending(event, "AAA", "T")]

        maybe_send_digest(changes, _ctx(event=event))

        assert mail.outbox[0].to == ["ops-fallback@example.com"]
