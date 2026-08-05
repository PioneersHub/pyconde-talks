"""
Question and Answer management module for conference talks.

This module provides models for allowing users to ask questions about talks, vote on questions, and
receive answers from speakers or moderators.
"""

from typing import TYPE_CHECKING, Any, ClassVar, Self

from django.conf import settings
from django.db import models
from django.db.models import Count
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from .models import Talk


if TYPE_CHECKING:
    from django_stubs_ext import StrOrPromise
    from django_stubs_ext.db.models.manager import RelatedManager

# Constants
CONTENT_PREVIEW_LENGTH = 50
# Upper bound for a question / answer body. Bounded (like Rating.comment) so a logged-in attendee
# cannot store a multi-megabyte body that is then re-rendered to every viewer of the Q&A page.
# Enforced by the auto-generated ModelForms (which map a max_length TextField to a length-validated
# CharField) on both the create and edit paths.
CONTENT_MAX_LENGTH = 2000


def _truncate_for_preview(text: str) -> str:
    """Return *text* truncated to CONTENT_PREVIEW_LENGTH chars with an ellipsis."""
    if len(text) > CONTENT_PREVIEW_LENGTH:
        return f"{text[:CONTENT_PREVIEW_LENGTH]}..."
    return text


class QuestionQuerySet(models.QuerySet["Question"]):  # type: ignore[call-arg]
    """Custom QuerySet for Question model with additional methods."""

    def with_vote_count(self) -> Self:
        """Annotate queryset with the count of votes."""
        return self.annotate(votes_count=Count("votes"))

    def sorted_by_votes(self) -> Self:
        """Return questions sorted by vote count (descending)."""
        return self.with_vote_count().order_by("-votes_count", "-created_at")

    def pending(self) -> Self:
        """Return only questions waiting for a moderator."""
        return self.filter(status=Question.Status.PENDING)

    def approved(self) -> Self:
        """Return only approved questions."""
        return self.filter(status=Question.Status.APPROVED)

    def answered(self) -> Self:
        """Return only answered questions."""
        return self.filter(status=Question.Status.ANSWERED)

    def not_rejected(self) -> Self:
        """Return questions that haven't been rejected."""
        return self.exclude(status=Question.Status.REJECTED)


class Question(models.Model):
    """Represents a question asked about a talk."""

    class Status(models.TextChoices):
        """Status of a question."""

        PENDING = "pending", _("Pending review")
        APPROVED = "approved", _("Approved")
        ANSWERED = "answered", _("Answered")
        REJECTED = "rejected", _("Rejected")

    talk = models.ForeignKey(
        Talk,
        on_delete=models.CASCADE,
        related_name="questions",
        help_text=_("Talk this question is about"),
    )

    content = models.TextField(
        max_length=CONTENT_MAX_LENGTH,
        help_text=_("The question text"),
    )

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="questions",
        help_text=_("User who asked the question (if logged in)"),
    )

    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        # Stays APPROVED so an event in the default OPEN mode publishes immediately, exactly as
        # before. Pre-moderation is opted into per event and applied by the view, not by
        # flipping this default: doing that would also make admin-created questions invisible.
        default=Status.APPROVED,
        help_text=_("Status of the question"),
    )

    flag_reason = models.CharField(
        max_length=100,
        blank=True,
        default="",
        help_text=_(
            "Why this question was held for review, when the spam heuristics caught it. "
            "Empty means it was not auto-flagged.",
        ),
    )

    created_at = models.DateTimeField(
        default=timezone.now,
        help_text=_("When this question was asked"),
    )

    updated_at = models.DateTimeField(
        auto_now=True,
        help_text=_("When this question was last modified"),
    )

    # Use our custom QuerySet manager
    objects: ClassVar[QuestionQuerySet] = QuestionQuerySet.as_manager()  # type: ignore[assignment]

    user_voted: bool  # Set in build_question_list_context
    votes_count: int  # Set by with_vote_count() queryset annotation
    votes: RelatedManager[QuestionVote]
    answers: RelatedManager[Answer]

    class Meta:
        """Metadata for the Question model."""

        ordering: ClassVar[list[str]] = ["-created_at"]
        verbose_name = _("Question")
        verbose_name_plural = _("Questions")
        indexes: ClassVar[list[models.Index]] = [
            models.Index(fields=["talk", "status"]),
            models.Index(fields=["user"]),
        ]

    def __str__(self) -> str:
        """Return a string representation of the question."""
        return _truncate_for_preview(self.content)

    @property
    def display_name(self) -> StrOrPromise:
        """Return the author's display name based on related user."""
        if not self.user:
            return _("Anonymous")
        return self.user.label(obfuscate=True) or _("Anonymous")

    @property
    def has_answer(self) -> bool:
        """Return True if this question has at least one answer."""
        return self.answers.exists()

    @property
    def vote_count(self) -> int:
        """Return the number of votes this question has received."""
        # Check if this instance has the annotation from the queryset
        if hasattr(self, "votes_count"):
            return self.votes_count
        # Otherwise calculate it dynamically
        return self.votes.count()

    def user_has_voted(self, user: models.Model | None) -> bool:
        """Check if a specific user has voted for this question."""
        if not user or getattr(user, "is_anonymous", True):
            return False
        return self.votes.filter(user=user).exists()

    def mark_as_answered(self) -> None:
        """Mark the question as answered."""
        self.status = self.Status.ANSWERED
        self.save(update_fields=["status", "updated_at"])

    def mark_as_pending(self, reason: str = "") -> None:
        """
        Hold the question for moderation, recording why.

        *reason* is stored on ``flag_reason`` so a moderator can see what caught it, and so a
        heuristic that misfires in production can be identified from the data rather than guessed
        at.
        """
        self.status = self.Status.PENDING
        self.flag_reason = reason
        self.save(update_fields=["status", "flag_reason", "updated_at"])

    def reject(self) -> None:
        """Reject the question."""
        self.status = self.Status.REJECTED
        self.save(update_fields=["status", "updated_at"])

    def approve(self) -> None:
        """
        Approve the question, clearing any auto-flag.

        A moderator saying yes settles the matter, so the flag should not linger and make the
        question look suspect in the admin afterwards.
        """
        self.status = self.Status.APPROVED
        self.flag_reason = ""
        self.save(update_fields=["status", "flag_reason", "updated_at"])


class QuestionVote(models.Model):
    """Represents a user's vote on a question."""

    question = models.ForeignKey(
        Question,
        on_delete=models.CASCADE,
        related_name="votes",
        help_text=_("Question being voted on"),
    )

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="question_votes",
        help_text=_("User who voted"),
    )

    created_at = models.DateTimeField(
        default=timezone.now,
        help_text=_("When this vote was created"),
    )

    class Meta:
        """Metadata for the QuestionVote model."""

        verbose_name = _("Question Vote")
        verbose_name_plural = _("Question Votes")
        indexes: ClassVar[list[models.Index]] = [
            models.Index(fields=["user"]),
        ]
        constraints: ClassVar[list[models.UniqueConstraint]] = [
            models.UniqueConstraint(fields=["question", "user"], name="unique_question_vote"),
        ]

    def __str__(self) -> str:
        """Return a string representation of the vote."""
        return f"Vote by {self.user} on question {self.question.pk}"


class Answer(models.Model):
    """Represents an answer to a question."""

    question = models.ForeignKey(
        Question,
        on_delete=models.CASCADE,
        related_name="answers",
        help_text=_("Question this answer responds to"),
    )

    content = models.TextField(
        max_length=CONTENT_MAX_LENGTH,
        help_text=_("The answer text"),
    )

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="answers",
        help_text=_("User who provided the answer"),
    )

    is_official = models.BooleanField(
        default=False,
        help_text=_("Whether this is an official answer from a speaker or organizer"),
    )

    created_at = models.DateTimeField(
        default=timezone.now,
        help_text=_("When this answer was created"),
    )

    updated_at = models.DateTimeField(
        auto_now=True,
        help_text=_("When this answer was last modified"),
    )

    class Meta:
        """Metadata for the Answer model."""

        ordering: ClassVar[list[str]] = ["created_at"]
        verbose_name = _("Answer")
        verbose_name_plural = _("Answers")
        indexes: ClassVar[list[models.Index]] = [
            models.Index(fields=["question"]),
            models.Index(fields=["user"]),
        ]

    def __str__(self) -> str:
        """Return a string representation of the answer."""
        return _truncate_for_preview(self.content)

    def save(self, *args: Any, **kwargs: Any) -> None:
        """
        Save the answer and update the question status if needed.

        When an answer is saved, the related question's status is updated to "answered" if it was
        already published.
        """
        super().save(*args, **kwargs)

        # Only a question that is already published moves to "answered". A pending one is still
        # waiting for a moderator, and promoting it here would publish it without the review it
        # was held for, leaving its ``flag_reason`` in place so it reads as flagged and approved
        # at the same time. Rejected questions stay rejected, as before.
        if self.question.status in (Question.Status.APPROVED, Question.Status.ANSWERED):
            self.question.status = Question.Status.ANSWERED
            self.question.save(update_fields=["status", "updated_at"])
