"""
Tests that ``?next=`` survives the passwordless login-code flow.

The gated-recording journey depends on it: "Sign in to watch" on a schedule-only talk is a
promise that signing in brings you back to that talk. The login templates are overridden in this
project, and both of them posted to a bare action URL with no redirect field, so allauth received
``next=None`` and sent everyone to the home page instead. The links looked right and the flow
quietly ignored them.
"""

import re
from datetime import timedelta
from http import HTTPStatus
from typing import TYPE_CHECKING
from urllib.parse import quote

import pytest
from django.urls import reverse
from django.utils import timezone
from model_bakery import baker

from events.models import Event
from talks.models import Talk
from users.models import CustomUser


if TYPE_CHECKING:
    from django.core.mail import EmailMessage
    from django.test import Client


@pytest.fixture
def public_talk() -> Talk:
    """Return a talk on a public event, with a slot in the past."""
    event = Event.objects.create(
        name="Public",
        slug="public",
        visibility=Event.Visibility.PUBLIC,
        is_active=True,
    )
    return baker.make(
        Talk,
        event=event,
        title="A recorded talk",
        start_time=timezone.now() - timedelta(days=1),
        duration=timedelta(minutes=30),
    )


def _code_from(mail: EmailMessage) -> str:
    """Pull the login code out of the email allauth sent."""
    match = re.search(r"code is: ([A-Z0-9-]+)", str(mail.body))
    assert match, f"no login code in: {mail.body!r}"
    return match.group(1)


@pytest.mark.django_db
class TestNextSurvivesTheLoginCodeFlow:
    """Both steps of the flow have to carry the redirect, not just the first."""

    def test_both_forms_render_the_redirect_field(
        self,
        client: Client,
        public_talk: Talk,
        mailoutbox: list[EmailMessage],
    ) -> None:
        """
        Each template must render the hidden field, or the POST drops the redirect.

        Checked separately from the end-to-end test below because this is the actual regression:
        a template that stops rendering it fails silently, sending people to the home page. The
        confirm page is fetched from inside the flow, since it redirects away without a pending
        login in the session.
        """
        CustomUser.objects.create_user(email="attendee@example.com")
        target = reverse("talk_detail", kwargs={"pk": public_talk.pk})

        request_url = f"{reverse('account_request_login_code')}?next={target}"
        request_page = client.get(request_url)
        assert request_page.status_code == HTTPStatus.OK
        assert b'name="next"' in request_page.content

        requested = client.post(
            request_url,
            {"email": "attendee@example.com", "event": public_talk.event.slug},
        )
        confirm_page = client.get(requested.headers["Location"])
        assert confirm_page.status_code == HTTPStatus.OK
        assert b'name="next"' in confirm_page.content
        assert _code_from(mailoutbox[-1])

    def test_signing_in_returns_to_the_talk(
        self,
        client: Client,
        public_talk: Talk,
        mailoutbox: list[EmailMessage],
    ) -> None:
        """End to end: ask for a code from a talk page, and land back on that talk."""
        CustomUser.objects.create_user(email="attendee@example.com")
        target = reverse("talk_detail", kwargs={"pk": public_talk.pk})

        request_url = f"{reverse('account_request_login_code')}?next={target}"
        requested = client.post(
            request_url,
            {"email": "attendee@example.com", "event": public_talk.event.slug},
        )
        assert requested.status_code == HTTPStatus.FOUND
        # The redirect to the confirm step keeps the target, so its form can render it. The
        # location carries it percent-encoded as a query parameter.
        assert quote(target, safe="") in requested.headers["Location"]

        confirmed = client.post(
            reverse("account_confirm_login_code"),
            {"code": _code_from(mailoutbox[-1]), "next": target},
        )

        assert confirmed.status_code == HTTPStatus.FOUND
        assert confirmed.headers["Location"] == target

    def test_a_plain_sign_in_still_lands_on_the_home_page(
        self,
        client: Client,
        mailoutbox: list[EmailMessage],
    ) -> None:
        """Without a target the default is unchanged, so the field cannot break ordinary logins."""
        CustomUser.objects.create_user(email="attendee@example.com")
        event = Event.objects.create(
            name="Other",
            slug="other",
            visibility=Event.Visibility.PUBLIC,
            is_active=True,
        )

        client.post(
            reverse("account_request_login_code"),
            {"email": "attendee@example.com", "event": event.slug},
        )
        confirmed = client.post(
            reverse("account_confirm_login_code"),
            {"code": _code_from(mailoutbox[-1])},
        )

        assert confirmed.status_code == HTTPStatus.FOUND
        assert confirmed.headers["Location"] == "/"
