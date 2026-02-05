"""Tests for talks.utils."""

import pytest
from model_bakery import baker

from talks.models import Talk
from talks.utils import get_talk_by_id_or_pretalx


@pytest.mark.django_db
class TestGetTalkByIdOrPretalx:
    """Tests for get_talk_by_id_or_pretalx."""

    def test_find_by_pk(self) -> None:
        """Find a talk by its numeric primary key."""
        talk = baker.make(Talk)
        result = get_talk_by_id_or_pretalx(str(talk.pk))
        assert result == talk

    def test_find_by_pretalx_id(self) -> None:
        """Find a talk by a substring matching its pretalx_link."""
        talk = baker.make(Talk, pretalx_link="https://pretalx.com/event/talk/DEMO3")
        result = get_talk_by_id_or_pretalx("DEMO3")
        assert result == talk

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
        result = get_talk_by_id_or_pretalx("ABC")
        assert result == talk
