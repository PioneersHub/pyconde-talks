"""Tests for talks.utils."""

import pytest
from model_bakery import baker

from events.models import Event
from talks.models import Talk
from talks.utils import get_talk_by_id_or_pretalx, pretalx_code_q
from users.models import CustomUser


def _superuser() -> CustomUser:
    """
    Return a superuser, for tests about lookup rather than about access.

    ``get_talk_by_id_or_pretalx`` always scopes to what the viewer may see, and talks default to a
    hidden event, so a viewer that sees everything keeps these tests focused on whether the pk /
    pretalx-link resolution works.
    """
    return CustomUser.objects.create_superuser(
        email="lookup-admin@example.com",
        password="password",
    )


@pytest.mark.django_db
class TestGetTalkByIdOrPretalx:
    """Tests for get_talk_by_id_or_pretalx."""

    def test_find_by_pk(self) -> None:
        """Find a talk by its numeric primary key."""
        talk = baker.make(Talk)
        result = get_talk_by_id_or_pretalx(str(talk.pk), user=_superuser())
        assert result == talk

    def test_find_by_pretalx_id(self) -> None:
        """Find a talk by a substring matching its pretalx_link."""
        talk = baker.make(Talk, pretalx_link="https://pretalx.com/event/talk/DEMO3")
        result = get_talk_by_id_or_pretalx("DEMO3", user=_superuser())
        assert result == talk

    def test_without_a_user_nothing_hidden_is_reachable(self) -> None:
        """
        Omitting *user* scopes to the anonymous view rather than to everything.

        The helper used to fall back to ``Talk.objects.all()``, which would now hand a talk on a
        hidden event to a caller that simply forgot to pass the viewer.
        """
        talk = baker.make(Talk)
        assert get_talk_by_id_or_pretalx(str(talk.pk)) is None

        talk.event.visibility = Event.Visibility.PUBLIC
        talk.event.save(update_fields=["visibility"])
        assert get_talk_by_id_or_pretalx(str(talk.pk)) == talk

    def test_not_found(self) -> None:
        """Return None when no talk matches the given identifier."""
        result = get_talk_by_id_or_pretalx("NONEXISTENT")
        assert result is None

    def test_pk_not_found_falls_back_to_pretalx(self) -> None:
        """Fall back to pretalx_link search when the numeric PK does not match."""
        result = get_talk_by_id_or_pretalx("999999")
        assert result is None

    def test_non_numeric_id(self) -> None:
        """Skip the PK lookup for non-numeric strings and search pretalx_link directly."""
        talk = baker.make(Talk, pretalx_link="https://pretalx.com/event/talk/ABC")
        result = get_talk_by_id_or_pretalx("ABC", user=_superuser())
        assert result == talk


@pytest.mark.django_db
class TestPretalxCodeQ:
    """Tests for pretalx_code_q."""

    @pytest.mark.parametrize(
        "link", ["https://p.com/e/talk/XYZ789", "https://p.com/e/talk/XYZ789/"]
    )
    @pytest.mark.parametrize("code", ["XYZ789", "xyz789"])
    def test_matches_whole_code(self, link: str, code: str) -> None:
        """Match the whole code case-insensitively, with or without a trailing slash."""
        talk = baker.make(Talk, pretalx_link=link)
        assert list(Talk.objects.filter(pretalx_code_q(code))) == [talk]

    @pytest.mark.parametrize("term", ["https", "p.com", "talk", "e/talk", "XYZ", "789", "YZ78"])
    def test_ignores_the_rest_of_the_url(self, term: str) -> None:
        """Never match on the host, the path, or a fragment of the code."""
        baker.make(Talk, pretalx_link="https://p.com/e/talk/XYZ789/")
        assert not Talk.objects.filter(pretalx_code_q(term)).exists()

    def test_empty_code_matches_nothing(self) -> None:
        """An empty code must not widen the queryset it is OR'd into."""
        baker.make(Talk, pretalx_link="https://p.com/e/talk/XYZ789/")
        assert not Talk.objects.filter(pretalx_code_q("")).exists()

    def test_talk_without_a_link_never_matches(self) -> None:
        """A talk with no pretalx link stays out of every code search."""
        baker.make(Talk, pretalx_link="")
        assert not Talk.objects.filter(pretalx_code_q("XYZ789")).exists()
