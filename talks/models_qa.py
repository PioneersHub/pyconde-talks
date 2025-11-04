"""
Question and Answer management module for conference talks.

This module provides models for allowing users to ask questions about talks, vote on questions, and
receive answers from speakers or moderators.
"""

from typing import Any, ClassVar

from django.conf import settings
from django.db import models
from django.db.models import Count, QuerySet
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from .models import Talk


# Constants
CONTENT_PREVIEW_LENGTH = 50


class QuestionQuerySet(models.QuerySet):
    """Custom QuerySet for Question model with additional methods."""

    def with_vote_count(self) -> QuerySet:
        """Annotate queryset with the count of votes."""
        return self.annotate(votes_count=Count("votes"))

    def sorted_by_votes(self) -> QuerySet:
        """Return questions sorted by vote count (descending)."""
        return self.with_vote_count().order_by("-votes_count", "-created_at")

    def approved(self) -> QuerySet:
        """Return only approved questions."""
        return self.filter(status=Question.Status.APPROVED)

    def answered(self) -> QuerySet:
        """Return only answered questions."""
        return self.filter(status=Question.Status.ANSWERED)

    def not_rejected(self) -> QuerySet:
        """Return questions that haven't been rejected."""
        return self.exclude(status=Question.Status.REJECTED)


class Question(models.Model):
    """Represents a question asked about a talk."""

    class Status(models.TextChoices):
        """Status of a question."""

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
        max_length=10,
        choices=Status.choices,
        default=Status.APPROVED,
        help_text=_("Status of the question"),
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
    objects = QuestionQuerySet.as_manager()

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
        if len(self.content) > CONTENT_PREVIEW_LENGTH:
            return f"{self.content[:CONTENT_PREVIEW_LENGTH]}..."
        return self.content

    @property
    def display_name(self) -> str:
        """Return the author's display name based on related user."""
        if not self.user:
            return _("Anonymous")
        name = (
            self.user.display_name.strip()
            or self.user.get_full_name().strip()
            or self.user.email.strip()
        )
        return name or _("Anonymous")

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
        if not user or user.is_anonymous:
            return False
        return self.votes.filter(user=user).exists()

    def mark_as_answered(self) -> None:
        """Mark the question as answered."""
        self.status = self.Status.ANSWERED
        self.save(update_fields=["status", "updated_at"])

    def reject(self) -> None:
        """Reject the question."""
        self.status = self.Status.REJECTED
        self.save(update_fields=["status", "updated_at"])

    def approve(self) -> None:
        """Approve the question."""
        self.status = self.Status.APPROVED
        self.save(update_fields=["status", "updated_at"])


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

        unique_together: ClassVar[list[str]] = ["question", "user"]
        verbose_name = _("Question Vote")
        verbose_name_plural = _("Question Votes")
        indexes: ClassVar[list[models.Index]] = [
            models.Index(fields=["question"]),
            models.Index(fields=["user"]),
        ]

    def __str__(self) -> str:
        """Return a string representation of the vote."""
        return f"Vote by {self.user} on question {self.question.id}"


class Answer(models.Model):
    """Represents an answer to a question."""

    question = models.ForeignKey(
        Question,
        on_delete=models.CASCADE,
        related_name="answers",
        help_text=_("Question this answer responds to"),
    )

    content = models.TextField(
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
        if len(self.content) > CONTENT_PREVIEW_LENGTH:
            return f"{self.content[:CONTENT_PREVIEW_LENGTH]}..."
        return self.content

    def save(self, *args: Any, **kwargs: Any) -> None:
        """
        Save the answer and update the question status if needed.

        When an answer is saved, the related question's status is updated to "answered" if it's not
        already rejected.
        """
        super().save(*args, **kwargs)

        # Update question status to "answered" if not rejected
        if self.question.status != Question.Status.REJECTED:
            self.question.status = Question.Status.ANSWERED
            self.question.save(update_fields=["status", "updated_at"])
