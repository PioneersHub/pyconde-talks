"""Tests for the Rating model, admin, views, and template tags."""

# ruff: noqa: PLR2004

from http import HTTPStatus
from typing import TYPE_CHECKING

import pytest
from django.contrib.admin.sites import AdminSite
from django.db import IntegrityError
from django.template import Context, Template
from django.test import RequestFactory
from django.urls import reverse
from django.utils import timezone
from model_bakery import baker

from talks.admin import RatingAdmin
from talks.models import MAX_RATING_SCORE, MIN_RATING_SCORE, Rating, Talk
from users.models import CustomUser


if TYPE_CHECKING:
    from django.test.client import Client


site = AdminSite()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture()
def user() -> CustomUser:
    """Create a regular user for testing."""
    return baker.make(CustomUser, email="rater@example.com")


@pytest.fixture()
def other_user() -> CustomUser:
    """Create another user for testing."""
    return baker.make(CustomUser, email="other@example.com")


@pytest.fixture()
def admin_user() -> CustomUser:
    """Create a superuser for admin testing."""
    return CustomUser.objects.create_superuser(
        email="admin@admin.com",
        password="admin123!",
    )


@pytest.fixture()
def talk() -> Talk:
    """Create a talk for testing."""
    return baker.make(Talk, title="Test Talk", start_time=timezone.now())


@pytest.fixture()
def rf() -> RequestFactory:
    """Return a Django RequestFactory for building test requests."""
    return RequestFactory()


# ---------------------------------------------------------------------------
# Rating Model Tests
# ---------------------------------------------------------------------------
@pytest.mark.django_db
class TestRatingModel:
    """Tests for the Rating model."""

    def test_create_rating(self, user: CustomUser, talk: Talk) -> None:
        """Create a rating with valid data."""
        rating = Rating.objects.create(talk=talk, user=user, score=4, comment="Great talk!")
        assert rating.score == 4
        assert rating.comment == "Great talk!"
        assert rating.talk == talk
        assert rating.user == user

    def test_str_representation(self, user: CustomUser, talk: Talk) -> None:
        """Return a meaningful string representation."""
        rating = Rating.objects.create(talk=talk, user=user, score=5)
        expected = f"{user} rated {talk}: 5/{MAX_RATING_SCORE}"
        assert str(rating) == expected

    def test_unique_constraint_per_user_per_talk(self, user: CustomUser, talk: Talk) -> None:
        """Enforce one rating per user per talk."""
        Rating.objects.create(talk=talk, user=user, score=3)
        with pytest.raises(IntegrityError):
            Rating.objects.create(talk=talk, user=user, score=5)

    def test_different_users_can_rate_same_talk(
        self,
        user: CustomUser,
        other_user: CustomUser,
        talk: Talk,
    ) -> None:
        """Allow different users to rate the same talk."""
        Rating.objects.create(talk=talk, user=user, score=4)
        Rating.objects.create(talk=talk, user=other_user, score=5)
        assert Rating.objects.filter(talk=talk).count() == 2

    def test_score_range_constraint_too_low(self, user: CustomUser, talk: Talk) -> None:
        """Reject scores below the minimum."""
        with pytest.raises(IntegrityError):
            Rating.objects.create(talk=talk, user=user, score=0)

    def test_score_range_constraint_too_high(self, user: CustomUser, talk: Talk) -> None:
        """Reject scores above the maximum."""
        with pytest.raises(IntegrityError):
            Rating.objects.create(talk=talk, user=user, score=6)

    def test_valid_score_boundaries(self, user: CustomUser, talk: Talk) -> None:
        """Accept scores at the valid boundaries (1 and 5)."""
        r1 = Rating.objects.create(talk=talk, user=user, score=MIN_RATING_SCORE)
        assert r1.score == MIN_RATING_SCORE

        talk2 = baker.make(Talk, start_time=timezone.now())
        r2 = Rating.objects.create(talk=talk2, user=user, score=MAX_RATING_SCORE)
        assert r2.score == MAX_RATING_SCORE

    def test_comment_optional(self, user: CustomUser, talk: Talk) -> None:
        """Allow ratings without a comment."""
        rating = Rating.objects.create(talk=talk, user=user, score=3)
        assert rating.comment == ""

    def test_created_at_auto_set(self, user: CustomUser, talk: Talk) -> None:
        """Set created_at automatically on creation."""
        rating = Rating.objects.create(talk=talk, user=user, score=4)
        assert rating.created_at is not None

    def test_updated_at_auto_set(self, user: CustomUser, talk: Talk) -> None:
        """Update updated_at automatically on save."""
        rating = Rating.objects.create(talk=talk, user=user, score=4)
        original_updated = rating.updated_at
        rating.score = 5
        rating.save()
        rating.refresh_from_db()
        assert rating.updated_at >= original_updated

    def test_cascade_delete_talk(self, user: CustomUser, talk: Talk) -> None:
        """Delete ratings when the associated talk is deleted."""
        Rating.objects.create(talk=talk, user=user, score=4)
        talk.delete()
        assert Rating.objects.count() == 0

    def test_cascade_delete_user(self, user: CustomUser, talk: Talk) -> None:
        """Delete ratings when the associated user is deleted."""
        Rating.objects.create(talk=talk, user=user, score=4)
        user.delete()
        assert Rating.objects.count() == 0

    def test_ordering_by_created_at_descending(
        self,
        user: CustomUser,
        other_user: CustomUser,
        talk: Talk,
    ) -> None:
        """Return ratings ordered by most recent first."""
        r1 = Rating.objects.create(talk=talk, user=user, score=3)
        r2 = Rating.objects.create(talk=talk, user=other_user, score=5)
        ratings = list(Rating.objects.all())
        assert ratings == [r2, r1]


# ---------------------------------------------------------------------------
# Rating Admin Tests
# ---------------------------------------------------------------------------
@pytest.mark.django_db
class TestRatingAdmin:
    """Tests for the RatingAdmin configuration."""

    def test_has_comment_true(self, user: CustomUser, talk: Talk) -> None:
        """Report True when comment is present."""
        rating = baker.make(Rating, talk=talk, user=user, score=4, comment="Nice!")
        admin_instance = RatingAdmin(Rating, site)
        assert admin_instance.has_comment(rating) is True

    def test_has_comment_false(self, user: CustomUser, talk: Talk) -> None:
        """Report False when comment is empty."""
        rating = baker.make(Rating, talk=talk, user=user, score=4, comment="")
        admin_instance = RatingAdmin(Rating, site)
        assert admin_instance.has_comment(rating) is False

    def test_list_display_fields(self) -> None:
        """Include expected fields in list display."""
        admin_instance = RatingAdmin(Rating, site)
        assert "talk" in admin_instance.list_display
        assert "user" in admin_instance.list_display
        assert "score" in admin_instance.list_display
        assert "has_comment" in admin_instance.list_display
        assert "created_at" in admin_instance.list_display


# ---------------------------------------------------------------------------
# Rating Views Tests
# ---------------------------------------------------------------------------
@pytest.mark.django_db
class TestRateTalkView:
    """Tests for the rate_talk view."""

    def test_submit_rating(self, client: Client, user: CustomUser, talk: Talk) -> None:
        """Submit a valid rating."""
        client.force_login(user)
        url = reverse("rate_talk", args=[talk.pk])
        response = client.post(url, {"score": "4", "comment": "Good talk!"})
        assert response.status_code == HTTPStatus.FOUND
        rating = Rating.objects.get(talk=talk, user=user)
        assert rating.score == 4
        assert rating.comment == "Good talk!"

    def test_update_existing_rating(
        self,
        client: Client,
        user: CustomUser,
        talk: Talk,
    ) -> None:
        """Update an existing rating when resubmitted."""
        Rating.objects.create(talk=talk, user=user, score=3, comment="OK")
        client.force_login(user)
        url = reverse("rate_talk", args=[talk.pk])
        response = client.post(url, {"score": "5", "comment": "Actually great!"})
        assert response.status_code == HTTPStatus.FOUND
        rating = Rating.objects.get(talk=talk, user=user)
        assert rating.score == 5
        assert rating.comment == "Actually great!"

    def test_invalid_score_value(
        self,
        client: Client,
        user: CustomUser,
        talk: Talk,
    ) -> None:
        """Reject invalid (non-numeric) score values."""
        client.force_login(user)
        url = reverse("rate_talk", args=[talk.pk])
        response = client.post(url, {"score": "abc"})
        assert response.status_code == HTTPStatus.FOUND
        assert Rating.objects.filter(talk=talk).count() == 0

    def test_score_out_of_range_too_high(
        self,
        client: Client,
        user: CustomUser,
        talk: Talk,
    ) -> None:
        """Reject scores above the maximum."""
        client.force_login(user)
        url = reverse("rate_talk", args=[talk.pk])
        response = client.post(url, {"score": "6"})
        assert response.status_code == HTTPStatus.FOUND
        assert Rating.objects.filter(talk=talk).count() == 0

    def test_score_out_of_range_too_low(
        self,
        client: Client,
        user: CustomUser,
        talk: Talk,
    ) -> None:
        """Reject scores below the minimum."""
        client.force_login(user)
        url = reverse("rate_talk", args=[talk.pk])
        response = client.post(url, {"score": "0"})
        assert response.status_code == HTTPStatus.FOUND
        assert Rating.objects.filter(talk=talk).count() == 0

    def test_missing_score(self, client: Client, user: CustomUser, talk: Talk) -> None:
        """Reject submissions without a score."""
        client.force_login(user)
        url = reverse("rate_talk", args=[talk.pk])
        response = client.post(url, {})
        assert response.status_code == HTTPStatus.FOUND
        assert Rating.objects.filter(talk=talk).count() == 0

    def test_unauthenticated_user_redirected(self, client: Client, talk: Talk) -> None:
        """Redirect unauthenticated users to login."""
        url = reverse("rate_talk", args=[talk.pk])
        response = client.post(url, {"score": "4"})
        assert response.status_code == HTTPStatus.FOUND

    def test_get_method_not_allowed(
        self,
        client: Client,
        user: CustomUser,
        talk: Talk,
    ) -> None:
        """Reject GET requests (POST only)."""
        client.force_login(user)
        url = reverse("rate_talk", args=[talk.pk])
        response = client.get(url)
        assert response.status_code == HTTPStatus.METHOD_NOT_ALLOWED

    def test_nonexistent_talk_404(self, client: Client, user: CustomUser) -> None:
        """Return 404 when the talk does not exist."""
        client.force_login(user)
        url = reverse("rate_talk", args=[99999])
        response = client.post(url, {"score": "4"})
        assert response.status_code == HTTPStatus.NOT_FOUND

    def test_comment_stripped(
        self,
        client: Client,
        user: CustomUser,
        talk: Talk,
    ) -> None:
        """Strip whitespace from comment."""
        client.force_login(user)
        url = reverse("rate_talk", args=[talk.pk])
        client.post(url, {"score": "4", "comment": "  spaces  "})
        rating = Rating.objects.get(talk=talk, user=user)
        assert rating.comment == "spaces"


@pytest.mark.django_db
class TestGetTalkRatingStatsView:
    """Tests for the get_talk_rating_stats JSON view."""

    def test_stats_with_ratings(
        self,
        client: Client,
        user: CustomUser,
        other_user: CustomUser,
        talk: Talk,
    ) -> None:
        """Return correct stats when ratings exist."""
        Rating.objects.create(talk=talk, user=user, score=4)
        Rating.objects.create(talk=talk, user=other_user, score=2)
        client.force_login(user)
        url = reverse("talk_rating_stats", args=[talk.pk])
        response = client.get(url)
        assert response.status_code == HTTPStatus.OK
        data = response.json()
        assert data["average_rating"] == 3.0
        assert data["rating_count"] == 2
        assert data["user_rating"]["score"] == 4

    def test_stats_no_ratings(
        self,
        client: Client,
        user: CustomUser,
        talk: Talk,
    ) -> None:
        """Return null average and zero count when no ratings."""
        client.force_login(user)
        url = reverse("talk_rating_stats", args=[talk.pk])
        response = client.get(url)
        data = response.json()
        assert data["average_rating"] is None
        assert data["rating_count"] == 0
        assert data["user_rating"] is None

    def test_unauthenticated_user_redirected(self, client: Client, talk: Talk) -> None:
        """Redirect unauthenticated users to login."""
        url = reverse("talk_rating_stats", args=[talk.pk])
        response = client.get(url)
        assert response.status_code == HTTPStatus.FOUND

    def test_nonexistent_talk_404(self, client: Client, user: CustomUser) -> None:
        """Return 404 when the talk does not exist."""
        client.force_login(user)
        url = reverse("talk_rating_stats", args=[99999])
        response = client.get(url)
        assert response.status_code == HTTPStatus.NOT_FOUND


@pytest.mark.django_db
class TestTalkDetailViewRating:
    """Tests for rating context in TalkDetailView."""

    def test_context_includes_rating_data(
        self,
        client: Client,
        user: CustomUser,
        talk: Talk,
    ) -> None:
        """Include rating stats and user rating in context."""
        Rating.objects.create(talk=talk, user=user, score=4, comment="Nice!")
        client.force_login(user)
        url = reverse("talk_detail", args=[talk.pk])
        response = client.get(url)
        assert response.status_code == HTTPStatus.OK
        assert response.context["rating_count"] == 1
        assert response.context["average_rating"] == 4.0
        assert response.context["user_rating"].score == 4

    def test_context_no_ratings(
        self,
        client: Client,
        user: CustomUser,
        talk: Talk,
    ) -> None:
        """Show zero count and no user rating when no ratings exist."""
        client.force_login(user)
        url = reverse("talk_detail", args=[talk.pk])
        response = client.get(url)
        assert response.context["rating_count"] == 0
        assert response.context["average_rating"] is None
        assert response.context["user_rating"] is None


@pytest.mark.django_db
class TestTalkListViewRating:
    """Tests for rating annotations in TalkListView."""

    def test_talks_annotated_with_ratings(
        self,
        client: Client,
        user: CustomUser,
        talk: Talk,
    ) -> None:
        """Annotate talks with average_rating and rating_count."""
        Rating.objects.create(talk=talk, user=user, score=5)
        client.force_login(user)
        url = reverse("talk_list")
        response = client.get(url)
        assert response.status_code == HTTPStatus.OK
        talk_obj = response.context["talks"][0]
        assert talk_obj.average_rating == 5.0
        assert talk_obj.rating_count == 1


# ---------------------------------------------------------------------------
# Template Tag Tests
# ---------------------------------------------------------------------------
class TestStarRatingTag:
    """Tests for the star_rating template tag."""

    @staticmethod
    def _render(average_rating: float | None, rating_count: int = 0) -> str:
        """Render the star_rating tag with the given values."""
        template = Template("{% load rating_tags %}{% star_rating avg count %}")
        ctx = Context({"avg": average_rating, "count": rating_count})
        return template.render(ctx)

    def test_no_ratings(self) -> None:
        """Display 'No ratings yet' when there are no ratings."""
        html = self._render(None, 0)
        assert "No ratings yet" in html

    def test_zero_count(self) -> None:
        """Display 'No ratings yet' when count is zero even with average."""
        html = self._render(4.5, 0)
        assert "No ratings yet" in html

    def test_full_stars(self) -> None:
        """Render five full stars for a 5.0 rating."""
        html = self._render(5.0, 10)
        assert "5.0" in html
        assert "10" in html
        # Should have 5 filled stars (text-yellow-400 fill-current)
        assert html.count("text-yellow-400 fill-current") == 5

    def test_half_star(self) -> None:
        """Render a half star for a 3.5 rating."""
        html = self._render(3.5, 5)
        assert "3.5" in html
        assert "half-star" in html

    def test_partial_rating(self) -> None:
        """Render correct stars for a 2.0 rating."""
        html = self._render(2.0, 3)
        assert "2.0" in html
        # 2 filled + 3 empty = 5 total
        assert html.count("text-yellow-400 fill-current") == 2
        assert html.count("text-gray-300 fill-current") == 3
