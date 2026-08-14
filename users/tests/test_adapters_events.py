"""Tests for event-aware email authorization in the custom adapter."""

from typing import TYPE_CHECKING, Any

import pytest
from model_bakery import baker

from events.models import Event
from users.adapters import AccountAdapter
from users.models import CustomUser, EventAccessGrant, grant_event_access


if TYPE_CHECKING:
    import respx
    from pytest_django import Settings


# Match the legacy respx_mock default (assert_all_called=False): a couple of tests register a
# route that an early-return path intentionally never calls.
pytestmark = pytest.mark.httpx2(assert_all_called=False)


@pytest.fixture
def adapter() -> AccountAdapter:
    """Return an AccountAdapter instance."""
    return AccountAdapter()


@pytest.fixture
def event_with_api() -> Event:
    """Return an active Event with a validation API URL."""
    return Event.objects.create(
        name="Event With API",
        slug="event-api",
        year=2025,
        validation_api_url="https://event-api.example.com/validate",
        is_active=True,
    )


@pytest.fixture
def event_without_api() -> Event:
    """Return an active Event without a validation API URL."""
    return Event.objects.create(
        name="Event No API",
        slug="event-no-api",
        year=2025,
        validation_api_url="",
        is_active=True,
    )


@pytest.mark.django_db
class TestSetSelectedEvent:
    """Tests for the set_selected_event method."""

    def test_set_event(self, adapter: AccountAdapter, event_with_api: Event) -> None:
        """Setting the selected event stores it on the adapter."""
        adapter.set_selected_event(event_with_api)
        assert adapter._selected_event == event_with_api

    def test_set_none(self, adapter: AccountAdapter) -> None:
        """Setting None clears the selected event."""
        adapter.set_selected_event(None)
        assert adapter._selected_event is None


@pytest.mark.django_db
class TestEventAwareAuthorization:
    """Tests for is_email_authorized with event-aware logic."""

    @pytest.fixture(autouse=True)
    def _no_oauth(self, settings: Settings) -> None:
        """Disable OAuth2 client credentials so API validation never makes a real token call."""
        settings.EMAIL_VALIDATION_API_OAUTH2_CLIENT_ID = ""
        settings.EMAIL_VALIDATION_API_OAUTH2_CLIENT_SECRET = ""
        settings.EMAIL_VALIDATION_API_OAUTH2_TOKEN_URL = ""

    def test_existing_user_linked_to_event_authorized(
        self,
        adapter: AccountAdapter,
        user_model: type[Any],
        event_with_api: Event,
        settings: Settings,
    ) -> None:
        """User already linked to the selected event is authorized immediately."""
        settings.AUTHORIZED_EMAILS_WHITELIST = []
        settings.EMAIL_VALIDATION_API_URL_FALLBACK = ""
        user = user_model.objects.create_user(email="linked@example.com")
        user.events.add(event_with_api)

        adapter.set_selected_event(event_with_api)
        assert adapter.is_email_authorized("linked@example.com") is True

    def test_existing_user_not_linked_no_api_denied(
        self,
        adapter: AccountAdapter,
        user_model: type[Any],
        event_without_api: Event,
        settings: Settings,
    ) -> None:
        """User NOT linked to event, no API configured -> denied."""
        settings.AUTHORIZED_EMAILS_WHITELIST = []
        settings.EMAIL_VALIDATION_API_URL_FALLBACK = ""
        user_model.objects.create_user(email="unlinked@example.com")

        adapter.set_selected_event(event_without_api)
        assert adapter.is_email_authorized("unlinked@example.com") is False

    def test_existing_user_not_linked_api_valid_links_user(
        self,
        adapter: AccountAdapter,
        user_model: type[Any],
        event_with_api: Event,
        settings: Settings,
        httpx2_mock: respx.Router,
    ) -> None:
        """User NOT linked, event API validates -> authorized AND linked to event."""
        settings.AUTHORIZED_EMAILS_WHITELIST = []
        settings.EMAIL_VALIDATION_API_URL_FALLBACK = ""
        user = user_model.objects.create_user(email="newticket@example.com")

        httpx2_mock.post(event_with_api.validation_api_url).respond(200, json={"valid": True})

        adapter.set_selected_event(event_with_api)
        assert adapter.is_email_authorized("newticket@example.com") is True
        # User should now be associated with the event
        assert user.events.filter(pk=event_with_api.pk).exists()

    def test_existing_user_not_linked_api_invalid_denied(
        self,
        adapter: AccountAdapter,
        user_model: type[Any],
        event_with_api: Event,
        settings: Settings,
        httpx2_mock: respx.Router,
    ) -> None:
        """User NOT linked, event API rejects -> denied, NOT linked."""
        settings.AUTHORIZED_EMAILS_WHITELIST = []
        settings.EMAIL_VALIDATION_API_URL_FALLBACK = ""
        user = user_model.objects.create_user(email="rejected@example.com")

        httpx2_mock.post(event_with_api.validation_api_url).respond(200, json={"valid": False})

        adapter.set_selected_event(event_with_api)
        assert adapter.is_email_authorized("rejected@example.com") is False
        assert not user.events.filter(pk=event_with_api.pk).exists()

    def test_new_user_api_valid(
        self,
        adapter: AccountAdapter,
        event_with_api: Event,
        settings: Settings,
        httpx2_mock: respx.Router,
    ) -> None:
        """Non-existent user, event API validates -> authorized (user created later)."""
        settings.AUTHORIZED_EMAILS_WHITELIST = []
        settings.EMAIL_VALIDATION_API_URL_FALLBACK = ""

        httpx2_mock.post(event_with_api.validation_api_url).respond(200, json={"valid": True})

        adapter.set_selected_event(event_with_api)
        assert adapter.is_email_authorized("brand-new@example.com") is True

    def test_event_api_url_takes_precedence_over_global(
        self,
        adapter: AccountAdapter,
        event_with_api: Event,
        settings: Settings,
        httpx2_mock: respx.Router,
    ) -> None:
        """Event-specific API URL is used instead of the global fallback."""
        global_url = "https://global-api.example.com/validate"
        settings.AUTHORIZED_EMAILS_WHITELIST = []
        settings.EMAIL_VALIDATION_API_URL_FALLBACK = global_url

        httpx2_mock.post(event_with_api.validation_api_url).respond(200, json={"valid": True})

        adapter.set_selected_event(event_with_api)
        assert adapter.is_email_authorized("user@example.com") is True
        # Should have called the event API, not the global one
        assert httpx2_mock.calls.call_count == 1
        assert str(httpx2_mock.calls[0].request.url) == event_with_api.validation_api_url

    def test_global_api_fallback_when_event_has_no_url(
        self,
        adapter: AccountAdapter,
        event_without_api: Event,
        settings: Settings,
        httpx2_mock: respx.Router,
    ) -> None:
        """When event has no API URL, global fallback is used."""
        global_url = "https://global-api.example.com/validate"
        settings.AUTHORIZED_EMAILS_WHITELIST = []
        settings.EMAIL_VALIDATION_API_URL_FALLBACK = global_url

        httpx2_mock.post(global_url).respond(200, json={"valid": True})

        adapter.set_selected_event(event_without_api)
        assert adapter.is_email_authorized("fallback@example.com") is True
        assert httpx2_mock.calls.call_count == 1
        assert str(httpx2_mock.calls[0].request.url) == global_url

    def test_no_event_no_api_denies(
        self,
        adapter: AccountAdapter,
        settings: Settings,
    ) -> None:
        """No event selected and no global API -> denied."""
        settings.AUTHORIZED_EMAILS_WHITELIST = []
        settings.EMAIL_VALIDATION_API_URL_FALLBACK = ""

        adapter.set_selected_event(None)
        assert adapter.is_email_authorized("nobody@example.com") is False

    def test_superuser_bypasses_event_check(
        self,
        adapter: AccountAdapter,
        user_model: type[Any],
        event_with_api: Event,
        settings: Settings,
    ) -> None:
        """Superusers are always authorized, regardless of event association."""
        settings.AUTHORIZED_EMAILS_WHITELIST = []
        settings.EMAIL_VALIDATION_API_URL_FALLBACK = ""
        user_model.objects.create_superuser(
            email="super@example.com",
            password="password",
        )
        adapter.set_selected_event(event_with_api)
        assert adapter.is_email_authorized("super@example.com") is True


@pytest.mark.django_db
class TestPublicEventOpenRegistration:
    """A public event drops the ticket check, because its content is already open."""

    @pytest.fixture(autouse=True)
    def _no_api(self, settings: Settings) -> None:
        """Remove every authorization shortcut so only the visibility rule can allow a login."""
        settings.AUTHORIZED_EMAILS_WHITELIST = []
        settings.EMAIL_VALIDATION_API_URL_FALLBACK = ""
        settings.EMAIL_VALIDATION_API_OAUTH2_CLIENT_ID = ""
        settings.EMAIL_VALIDATION_API_OAUTH2_CLIENT_SECRET = ""
        settings.EMAIL_VALIDATION_API_OAUTH2_TOKEN_URL = ""

    @pytest.fixture
    def public_event(self) -> Event:
        """Return an active public event with no validation API configured."""
        return Event.objects.create(
            name="Public Event",
            slug="public-event",
            year=2025,
            validation_api_url="",
            is_active=True,
            visibility=Event.Visibility.PUBLIC,
        )

    def test_unknown_email_is_allowed(
        self,
        adapter: AccountAdapter,
        public_event: Event,
    ) -> None:
        """
        Anyone may register for a public event.

        Without this the recordings would be readable but nobody new could ever ask a question,
        which is the only thing a login still buys on such an event.
        """
        adapter.set_selected_event(public_event)
        assert adapter.is_email_authorized("stranger@example.com") is True

    def test_existing_user_is_linked_to_the_event(
        self,
        adapter: AccountAdapter,
        user_model: type[Any],
        public_event: Event,
    ) -> None:
        """An existing account gains access to the public event on login."""
        user = user_model.objects.create_user(email="returning@example.com")
        assert user.events.filter(pk=public_event.pk).exists() is False

        adapter.set_selected_event(public_event)
        assert adapter.is_email_authorized("returning@example.com") is True
        assert user.events.filter(pk=public_event.pk).exists() is True

    def test_deactivated_account_is_still_denied(
        self,
        adapter: AccountAdapter,
        user_model: type[Any],
        public_event: Event,
    ) -> None:
        """
        A ban outranks open registration.

        Order of checks matters here: the is_active test has to come first, or making an event
        public would quietly readmit everyone who had been removed.
        """
        user = user_model.objects.create_user(email="banned@example.com")
        user.is_active = False
        user.save(update_fields=["is_active"])

        adapter.set_selected_event(public_event)
        assert adapter.is_email_authorized("banned@example.com") is False

    def test_no_validation_api_is_called(
        self,
        adapter: AccountAdapter,
        public_event: Event,
        respx_mock: respx.MockRouter,
    ) -> None:
        """The ticket API is skipped entirely rather than called and ignored."""
        public_event.validation_api_url = "https://tickets.example.com/validate"
        public_event.save(update_fields=["validation_api_url"])
        route = respx_mock.post("https://tickets.example.com/validate")

        adapter.set_selected_event(public_event)
        assert adapter.is_email_authorized("stranger@example.com") is True
        assert route.called is False

    @pytest.mark.parametrize(
        "visibility",
        [Event.Visibility.HIDDEN, Event.Visibility.SCHEDULE_ONLY],
    )
    def test_other_visibilities_still_require_a_ticket(
        self,
        adapter: AccountAdapter,
        visibility: str,
    ) -> None:
        """Only PUBLIC opens registration; schedule-only still gates on the ticket API."""
        event = Event.objects.create(
            name="Gated",
            slug=f"gated-{visibility}",
            is_active=True,
            visibility=visibility,
        )
        adapter.set_selected_event(event)
        assert adapter.is_email_authorized("stranger@example.com") is False

    def test_can_login_by_email_is_true_while_an_event_is_public(
        self,
        adapter: AccountAdapter,
    ) -> None:
        """
        Disconnecting a social account is safe when anyone can sign in by email.

        ``can_login_by_email`` guards the Discord disconnect flow; without this a user on a public
        event would be told they cannot safely disconnect when in fact they can.
        """
        Event.objects.create(
            name="Public",
            slug="public-live",
            is_active=True,
            visibility=Event.Visibility.PUBLIC,
        )
        assert adapter.can_login_by_email("anyone@example.com") is True


@pytest.mark.django_db
class TestAccessProvenanceIsRecorded:
    """
    Every membership handed out by the login flows records how it was granted.

    Without it an account let in by open registration on a public event is indistinguishable from a
    ticket holder, so taking that event back off public visibility would silently leave behind
    members who were never checked, with no way to find them again.
    """

    def test_open_registration_is_recorded_as_such(self, adapter: AccountAdapter) -> None:
        """A public event grants access with no ticket check, and says so."""
        event = Event.objects.create(
            name="Archive",
            slug="archive",
            visibility=Event.Visibility.PUBLIC,
            is_active=True,
        )
        user = baker.make(CustomUser, email="anyone@example.com")
        adapter.set_selected_event(event)

        assert adapter.is_email_authorized("anyone@example.com") is True

        grant = EventAccessGrant.objects.get(user=user, event=event)
        assert grant.source == EventAccessGrant.Source.OPEN_REGISTRATION
        assert grant.was_ticket_checked is False

    def test_a_repeat_login_keeps_the_original_source(self, adapter: AccountAdapter) -> None:
        """
        A second sign-in must not rewrite history.

        Someone ticket-checked last year does not become an open-registration member because they
        signed in again after the event was made public.
        """
        event = Event.objects.create(
            name="Archive",
            slug="archive",
            visibility=Event.Visibility.PUBLIC,
            is_active=True,
        )
        user = baker.make(CustomUser, email="member@example.com")
        grant_event_access(user, event, EventAccessGrant.Source.TICKET)
        adapter.set_selected_event(event)

        assert adapter.is_email_authorized("member@example.com") is True

        grant = EventAccessGrant.objects.get(user=user, event=event)
        assert grant.source == EventAccessGrant.Source.TICKET
        assert EventAccessGrant.objects.filter(user=user, event=event).count() == 1

    def test_granting_twice_makes_one_record(self) -> None:
        """The helper is idempotent, so a retried flow does not duplicate the row."""
        event = Event.objects.create(name="Event", slug="event", is_active=True)
        user = baker.make(CustomUser, email="someone@example.com")

        grant_event_access(user, event, EventAccessGrant.Source.TICKET)
        grant_event_access(user, event, EventAccessGrant.Source.TICKET)

        assert EventAccessGrant.objects.filter(user=user, event=event).count() == 1
        assert event in user.events.all()

    def test_a_ticket_checked_grant_reads_as_checked(self) -> None:
        """The property the admin leans on, for both of the verified sources."""
        event = Event.objects.create(name="Event", slug="event", is_active=True)
        user = baker.make(CustomUser, email="someone@example.com")
        grant_event_access(user, event, EventAccessGrant.Source.TICKET)

        assert EventAccessGrant.objects.get(user=user).was_ticket_checked is True

    def test_membership_without_a_grant_is_left_alone(self) -> None:
        """
        An event assigned by hand in the admin has no grant row, and that is the answer.

        Inventing an "unknown" source would claim the flows recorded something they did not.
        """
        event = Event.objects.create(name="Event", slug="event", is_active=True)
        user = baker.make(CustomUser, email="someone@example.com")
        user.events.add(event)

        assert event in user.events.all()
        assert not EventAccessGrant.objects.filter(user=user).exists()
