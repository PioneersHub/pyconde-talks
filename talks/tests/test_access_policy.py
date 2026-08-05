"""
The contract for which URLs are reachable without logging in.

This is a policy test rather than a behaviour test: it exists so that opening a URL to anonymous
visitors has to be a deliberate edit here as well as in ``talks/urls.py``. Q&A and ratings are
checked against a *public* event on purpose, because "public event" must not imply "public Q&A" -
moderating is volunteer work, and that is the whole reason those stay closed.
"""

from http import HTTPStatus
from typing import TYPE_CHECKING

import pytest
from django.urls import reverse
from model_bakery import baker

from events.models import Event
from talks.models import Talk
from talks.models_qa import Question
from users.models import CustomUser


if TYPE_CHECKING:
    from django.test import Client


# (url name, kwargs needing the talk pk, http method). Everything here must redirect an
# anonymous visitor to the login page even when the event is fully public.
CLOSED_ENDPOINTS = [
    ("talk_questions", "talk", "get"),
    ("question_create", "talk", "post"),
    ("question_vote", "question", "post"),
    ("question_edit", "question", "post"),
    ("question_delete", "question", "post"),
    ("question_reject", "question", "post"),
    ("question_approve", "question", "post"),
    ("question_mark_answered", "question", "post"),
    ("rate_talk", "talk", "post"),
    ("delete_rating", "talk", "post"),
    ("talk_rating_stats", "talk", "get"),
    ("toggle_save_talk", "talk", "post"),
    ("toggle_session_chair", "talk", "post"),
    ("chair_grid", "none", "get"),
]

# Read-only browsing, reachable without an account. Each one scopes its own contents, so the
# check here is only that the door is open; the leak tests live in test_anonymous_access.py.
OPEN_ENDPOINTS = [
    ("talk_list", "none"),
    ("schedule", "none"),
    ("upcoming_talks", "none"),
    ("dashboard_stats", "none"),
    ("talk_detail", "pk"),
]


@pytest.fixture
def public_talk() -> Talk:
    """Return a talk on a fully public event, the hardest case for the closed endpoints."""
    event = Event.objects.create(
        name="Public Event",
        slug="public-event",
        visibility=Event.Visibility.PUBLIC,
    )
    return baker.make(Talk, event=event, title="A public talk")


@pytest.mark.django_db
@pytest.mark.parametrize(("url_name", "kwarg_kind", "method"), CLOSED_ENDPOINTS)
def test_endpoint_stays_closed_to_anonymous(
    client: Client,
    public_talk: Talk,
    url_name: str,
    kwarg_kind: str,
    method: str,
) -> None:
    """Every non-browsing endpoint redirects anonymous visitors, even on a public event."""
    if kwarg_kind == "talk":
        url = reverse(url_name, kwargs={"talk_id": public_talk.pk})
    elif kwarg_kind == "question":
        question = baker.make(
            Question,
            talk=public_talk,
            user=baker.make(CustomUser, email="asker@example.com"),
        )
        url = reverse(url_name, kwargs={"question_id": question.pk})
    else:
        url = reverse(url_name)

    response = getattr(client, method)(url)

    assert response.status_code == HTTPStatus.FOUND, f"{url_name} did not redirect"
    assert "/accounts/login/" in response.headers.get("Location", ""), (
        f"{url_name} redirected somewhere other than the login page"
    )


@pytest.mark.django_db
@pytest.mark.parametrize(("url_name", "kwarg_kind"), OPEN_ENDPOINTS)
def test_endpoint_is_open_to_anonymous(
    client: Client,
    public_talk: Talk,
    url_name: str,
    kwarg_kind: str,
) -> None:
    """Browsing endpoints answer anonymous visitors directly instead of redirecting."""
    url = (
        reverse(url_name, kwargs={"pk": public_talk.pk})
        if kwarg_kind == "pk"
        else reverse(url_name)
    )

    response = client.get(url)

    assert response.status_code == HTTPStatus.OK, f"{url_name} did not answer an anonymous request"


@pytest.mark.django_db
def test_talk_detail_url_uses_pk_not_talk_id(public_talk: Talk) -> None:
    """
    Guard the kwarg name the parametrized test relies on.

    ``talk_detail`` takes ``pk`` while the endpoints above take ``talk_id``; if that ever changes,
    ``reverse`` would fail loudly here rather than silently skipping coverage.
    """
    assert reverse("talk_detail", kwargs={"pk": public_talk.pk})
