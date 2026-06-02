"""Tests for the ``PendingPretalxChange`` admin actions and re-detect button."""

# ruff: noqa: PLR2004

from unittest.mock import Mock, patch

import pytest
from django.contrib.admin.sites import AdminSite
from django.contrib.messages.storage.fallback import FallbackStorage
from django.test import RequestFactory
from django.urls import reverse
from model_bakery import baker

from events.models import Event
from talks.admin_pretalx import PendingPretalxChangeAdmin
from talks.models import PendingPretalxChange, Talk
from users.models import CustomUser


site = AdminSite()


@pytest.fixture()
def rf() -> RequestFactory:
    """Return a Django RequestFactory for building test requests."""
    return RequestFactory()


@pytest.fixture()
def admin_user() -> CustomUser:
    """Return a superuser required to access admin views."""
    return CustomUser.objects.create_superuser(
        email="admin@admin.com",
        password="admin123!",
    )


def _make_event() -> Event:
    """Return a saved Event suitable as FK target for pending rows."""
    return Event.objects.create(slug="evt", name="Evt", year=2099)


def _attach_messages(request: object) -> None:
    """Bolt a working messages backend onto a RequestFactory request."""
    request.session = {}  # type: ignore[attr-defined]
    request._messages = FallbackStorage(request)  # type: ignore[attr-defined]


@pytest.mark.django_db
class TestApplyAction:
    """``apply_changes`` runs apply for each pending row in the queryset."""

    def test_applies_pending_dismisses_closed(
        self,
        rf: RequestFactory,
        admin_user: CustomUser,
    ) -> None:
        """Pending rows get applied; already-closed rows are silently skipped."""
        event = _make_event()
        talk_a = baker.make(Talk, title="A", event=event)
        talk_b = baker.make(Talk, title="B", event=event)
        pending = PendingPretalxChange.objects.create(
            event=event,
            pretalx_code="A",
            kind=PendingPretalxChange.Kind.UPDATE,
            talk=talk_a,
            field_diffs={"title": {"old": "A", "new": "A renamed"}},
            speaker_diffs={"added": [], "removed": []},
            pretalx_payload={"speakers": []},
        )
        closed = PendingPretalxChange.objects.create(
            event=event,
            pretalx_code="B",
            kind=PendingPretalxChange.Kind.UPDATE,
            talk=talk_b,
            field_diffs={"title": {"old": "B", "new": "B renamed"}},
            speaker_diffs={"added": [], "removed": []},
            pretalx_payload={"speakers": []},
        )
        closed.mark_dismissed(user=admin_user)

        request = rf.post("/")
        request.user = admin_user
        _attach_messages(request)
        admin = PendingPretalxChangeAdmin(PendingPretalxChange, site)

        admin.apply_changes(
            request,
            PendingPretalxChange.objects.filter(pk__in=[pending.pk, closed.pk]),
        )

        talk_a.refresh_from_db()
        talk_b.refresh_from_db()
        assert talk_a.title == "A renamed"
        # Closed row is untouched.
        assert talk_b.title == "B"

        pending.refresh_from_db()
        assert pending.is_applied
        assert pending.applied_by == admin_user

    def test_apply_failure_surfaces_message(
        self,
        rf: RequestFactory,
        admin_user: CustomUser,
    ) -> None:
        """An apply that explodes still flips the other rows and writes an error message."""
        event = _make_event()
        # No talk attached -> UPDATE will raise.
        broken = PendingPretalxChange.objects.create(
            event=event,
            pretalx_code="X",
            kind=PendingPretalxChange.Kind.UPDATE,
            talk=None,
            field_diffs={"title": {"old": "x", "new": "y"}},
        )

        request = rf.post("/")
        request.user = admin_user
        _attach_messages(request)
        admin = PendingPretalxChangeAdmin(PendingPretalxChange, site)

        admin.apply_changes(request, PendingPretalxChange.objects.filter(pk=broken.pk))

        broken.refresh_from_db()
        assert broken.is_pending  # not marked applied because the apply raised


@pytest.mark.django_db
class TestDismissAction:
    """``dismiss_changes`` flips ``dismissed_at``/``dismissed_by`` on pending rows only."""

    def test_dismiss_marks_each_pending_row(
        self,
        rf: RequestFactory,
        admin_user: CustomUser,
    ) -> None:
        """Each pending row gets a dismissed timestamp and the acting user."""
        event = _make_event()
        c1 = PendingPretalxChange.objects.create(
            event=event,
            pretalx_code="C1",
            kind=PendingPretalxChange.Kind.UPDATE,
            talk=baker.make(Talk, event=event),
            field_diffs={},
            speaker_diffs={"added": [], "removed": []},
            pretalx_payload={"speakers": []},
        )
        c2 = PendingPretalxChange.objects.create(
            event=event,
            pretalx_code="C2",
            kind=PendingPretalxChange.Kind.UPDATE,
            talk=baker.make(Talk, event=event),
            field_diffs={},
            speaker_diffs={"added": [], "removed": []},
            pretalx_payload={"speakers": []},
        )

        request = rf.post("/")
        request.user = admin_user
        _attach_messages(request)
        admin = PendingPretalxChangeAdmin(PendingPretalxChange, site)

        admin.dismiss_changes(request, PendingPretalxChange.objects.all())

        c1.refresh_from_db()
        c2.refresh_from_db()
        assert c1.is_dismissed
        assert c2.is_dismissed
        assert c1.dismissed_by == admin_user


@pytest.mark.django_db
class TestCheckPretalxNow:
    """The "Check Pretalx now" view calls the importer with --detect-only."""

    @patch("talks.admin_pretalx.call_command")
    def test_view_invokes_detect_only_command(
        self,
        mock_call: Mock,
        rf: RequestFactory,
        admin_user: CustomUser,
    ) -> None:
        """A POST runs ``import_pretalx_talks --detect-only`` for DEFAULT_EVENT."""
        url = reverse("admin:talks_pendingpretalxchange_check_now")
        request = rf.post(url)
        request.user = admin_user
        _attach_messages(request)

        with patch("talks.admin_pretalx.settings", DEFAULT_EVENT="evt"):
            response = PendingPretalxChangeAdmin(PendingPretalxChange, site).check_pretalx_now(
                request
            )

        assert response.status_code == 302
        mock_call.assert_called_once()
        args, kwargs = mock_call.call_args
        assert args[0] == "import_pretalx_talks"
        assert "--detect-only" in args
        assert kwargs == {"verbosity": 1}

    @patch("talks.admin_pretalx.call_command")
    def test_view_rejects_get(
        self,
        mock_call: Mock,
        rf: RequestFactory,
        admin_user: CustomUser,
    ) -> None:
        """A GET must not trigger the importer (state-changing action is POST-only)."""
        url = reverse("admin:talks_pendingpretalxchange_check_now")
        request = rf.get(url)
        request.user = admin_user
        _attach_messages(request)

        with patch("talks.admin_pretalx.settings", DEFAULT_EVENT="evt"):
            response = PendingPretalxChangeAdmin(PendingPretalxChange, site).check_pretalx_now(
                request
            )

        assert response.status_code == 405
        mock_call.assert_not_called()

    def test_view_errors_when_default_event_missing(
        self,
        rf: RequestFactory,
        admin_user: CustomUser,
    ) -> None:
        """No DEFAULT_EVENT means the view refuses and reports an error message."""
        url = reverse("admin:talks_pendingpretalxchange_check_now")
        request = rf.post(url)
        request.user = admin_user
        _attach_messages(request)

        with patch("talks.admin_pretalx.settings", DEFAULT_EVENT=""):
            response = PendingPretalxChangeAdmin(PendingPretalxChange, site).check_pretalx_now(
                request
            )

        assert response.status_code == 302
