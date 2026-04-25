"""
Rating and bookmark models for conference talks.

Split out from ``talks.models`` so the Rating/SavedTalk pair (and their constants) live next to
each other. Uses the string reference ``"talks.Talk"`` for the foreign key so there is no import
cycle with ``talks.models``.
"""

from typing import TYPE_CHECKING, ClassVar

from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _


if TYPE_CHECKING:
    from users.models import CustomUser


# Rating score bounds (inclusive)
MIN_RATING_SCORE = 1
MAX_RATING_SCORE = 5
# Upper bound for the free-text comment attached to a rating
COMMENT_MAX_LENGTH = 2000


class Rating(models.Model):
    """Represents a user's rating of a talk, with an optional comment visible only to admins."""

    talk = models.ForeignKey(
        "talks.Talk",
        on_delete=models.CASCADE,
        related_name="ratings",
        help_text=_("The talk being rated"),
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="ratings",
        help_text=_("The user who submitted the rating"),
    )
    score = models.PositiveSmallIntegerField(
        help_text=_("Rating score from 1 to 5"),
    )
    comment = models.TextField(
        blank=True,
        max_length=COMMENT_MAX_LENGTH,
        help_text=_("Optional comment about the talk (visible only to admins)"),
    )
    created_at = models.DateTimeField(
        default=timezone.now,
        help_text=_("When this rating was created"),
    )
    updated_at = models.DateTimeField(
        auto_now=True,
        help_text=_("When this rating was last modified"),
    )

    class Meta:
        """Metadata for the Rating model."""

        verbose_name = _("Rating")
        verbose_name_plural = _("Ratings")
        ordering: ClassVar[list[str]] = ["-created_at"]
        indexes: ClassVar[list[models.Index]] = [
            models.Index(fields=["talk", "user"]),
            models.Index(fields=["talk", "-created_at"]),
        ]
        constraints: ClassVar[list[models.CheckConstraint | models.UniqueConstraint]] = [
            models.UniqueConstraint(
                fields=["talk", "user"],
                name="unique_user_talk_rating",
            ),
            models.CheckConstraint(
                condition=models.Q(score__gte=MIN_RATING_SCORE, score__lte=MAX_RATING_SCORE),
                name="rating_score_range",
            ),
        ]

    def __str__(self) -> str:
        """Return a string representation of the rating."""
        return f"{self.user} rated {self.talk}: {self.score}/{MAX_RATING_SCORE}"


class SavedTalk(models.Model):
    """Represents a user's saved/bookmarked talk."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="saved_talks",
        help_text=_("The user who saved the talk"),
    )
    talk = models.ForeignKey(
        "talks.Talk",
        on_delete=models.CASCADE,
        related_name="saved_by",
        help_text=_("The saved talk"),
    )
    created_at = models.DateTimeField(
        default=timezone.now,
        help_text=_("When this talk was saved"),
    )

    class Meta:
        """Metadata for the SavedTalk model."""

        verbose_name = _("Saved Talk")
        verbose_name_plural = _("Saved Talks")
        ordering: ClassVar[list[str]] = ["-created_at"]
        indexes: ClassVar[list[models.Index]] = [
            models.Index(fields=["user", "talk"]),
        ]
        constraints: ClassVar[list[models.UniqueConstraint]] = [
            models.UniqueConstraint(
                fields=["user", "talk"],
                name="unique_user_saved_talk",
            ),
        ]

    def __str__(self) -> str:
        """Return a string representation of the saved talk."""
        return f"{self.user} saved {self.talk}"

    @classmethod
    def talk_ids_for(cls, user: CustomUser) -> set[int]:
        """
        Return the set of ``Talk`` primary keys this user has saved.

        Templates use the result for fast ``talk.pk in saved_talk_ids`` membership checks on each
        row of a talk list, which is why a ``set`` (not a queryset) is returned.
        """
        return set(cls.objects.filter(user=user).values_list("talk_id", flat=True))
