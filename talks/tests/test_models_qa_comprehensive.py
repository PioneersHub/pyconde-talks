"""Comprehensive tests for talks.models_qa covering all uncovered branches."""
# ruff: noqa: SLF001 PLR2004

import pytest
from model_bakery import baker

from talks.models import Talk
from talks.models_qa import Answer, Question, QuestionVote
from users.models import CustomUser


@pytest.mark.django_db
class TestQuestionQuerySet:
    """Verify custom QuerySet methods for filtering and annotating questions."""

    def test_with_vote_count(self) -> None:
        """Annotate each question with its total vote count."""
        talk = baker.make(Talk)
        q = baker.make(Question, talk=talk)
        user = baker.make(CustomUser)
        baker.make(QuestionVote, question=q, user=user)
        qs = Question.objects.filter(talk=talk).with_vote_count()
        first = qs.first()
        assert first is not None
        assert first.votes_count == 1

    def test_sorted_by_votes(self) -> None:
        """Order questions by descending vote count."""
        talk = baker.make(Talk)
        q1 = baker.make(Question, talk=talk, content="First")
        q2 = baker.make(Question, talk=talk, content="Second")
        user1 = baker.make(CustomUser, email="a@example.com")
        user2 = baker.make(CustomUser, email="b@example.com")
        baker.make(QuestionVote, question=q2, user=user1)
        baker.make(QuestionVote, question=q2, user=user2)
        baker.make(QuestionVote, question=q1, user=user1)
        result = list(Question.objects.filter(talk=talk).sorted_by_votes())
        assert result[0] == q2

    def test_approved(self) -> None:
        """Filter to only questions with APPROVED status."""
        talk = baker.make(Talk)
        baker.make(Question, talk=talk, status=Question.Status.APPROVED)
        baker.make(Question, talk=talk, status=Question.Status.REJECTED)
        assert Question.objects.filter(talk=talk).approved().count() == 1

    def test_answered(self) -> None:
        """Filter to only questions with ANSWERED status."""
        talk = baker.make(Talk)
        baker.make(Question, talk=talk, status=Question.Status.ANSWERED)
        baker.make(Question, talk=talk, status=Question.Status.APPROVED)
        assert Question.objects.filter(talk=talk).answered().count() == 1

    def test_not_rejected(self) -> None:
        """Exclude REJECTED questions, keeping APPROVED and ANSWERED."""
        talk = baker.make(Talk)
        baker.make(Question, talk=talk, status=Question.Status.APPROVED)
        baker.make(Question, talk=talk, status=Question.Status.REJECTED)
        baker.make(Question, talk=talk, status=Question.Status.ANSWERED)
        assert Question.objects.filter(talk=talk).not_rejected().count() == 2


@pytest.mark.django_db
class TestQuestionModel:
    """Tests for the Question model."""

    def test_str_short(self) -> None:
        """Return the full content as the string representation for short text."""
        q = baker.make(Question, content="Short question")
        assert str(q) == "Short question"

    def test_str_long(self) -> None:
        """Truncate the string representation to 50 characters with an ellipsis."""
        q = baker.make(Question, content="x" * 100)
        result = str(q)
        assert result.endswith("...")
        assert len(result) == 53  # 50 + "..."

    def test_display_name_with_display_name(self) -> None:
        """Use the user's display_name when set."""
        user = baker.make(CustomUser, display_name="Alice")
        q = baker.make(Question, user=user)
        assert q.display_name == "Alice"

    def test_display_name_with_full_name(self) -> None:
        """Fall back to first_name + last_name when no display_name is set."""
        user = baker.make(CustomUser, display_name="", first_name="Jane", last_name="Doe")
        q = baker.make(Question, user=user)
        assert q.display_name == "Jane Doe"

    def test_display_name_with_email(self) -> None:
        """Fall back to an obfuscated email when no name fields are set."""
        user = baker.make(
            CustomUser,
            display_name="",
            first_name="",
            last_name="",
            email="john.doe@example.com",
        )
        q = baker.make(Question, user=user)
        result = q.display_name
        assert "@" in str(result)
        assert "***" in str(result)

    def test_display_name_no_user(self) -> None:
        """Return 'Anonymous' when the question has no associated user."""
        q = baker.make(Question, user=None)
        assert str(q.display_name) == "Anonymous"

    def test_obfuscate_email_empty(self) -> None:
        """Return an empty string for an empty email input."""
        assert Question._obfuscate_email("") == ""

    def test_obfuscate_email_no_at(self) -> None:
        """Mask the text as a single token when there is no @ sign."""
        assert Question._obfuscate_email("notanemail") == "n***l"

    def test_obfuscate_email_single_label_domain(self) -> None:
        """Handle a single-label domain with no dot (e.g., 'localhost')."""
        result = Question._obfuscate_email("user@localhost")
        assert "@" in result
        parts = result.split("@")
        assert "." not in parts[1]

    def test_obfuscate_email_short(self) -> None:
        """Replace single-character local and domain parts with asterisks."""
        assert Question._obfuscate_email("a@b.com") == "*@*.com"

    def test_obfuscate_email_medium(self) -> None:
        """Keep the first character visible for two-character local and domain parts."""
        assert Question._obfuscate_email("ab@xy.org") == "a*@x*.org"

    def test_obfuscate_email_full(self) -> None:
        """Keep first and last characters visible, masking the middle."""
        assert Question._obfuscate_email("john.doe@example.com") == "j***e@e***e.com"

    def test_obfuscate_email_subdomains(self) -> None:
        """Collapse subdomains into the domain mask, preserving only the TLD."""
        assert Question._obfuscate_email("user@mail.example.co.uk") == "u***r@c*.uk"

    def test_mask_token_empty(self) -> None:
        """Return an empty string for an empty token."""
        assert Question._mask_token("") == ""

    def test_mask_token_one(self) -> None:
        """Replace a single-character token entirely with an asterisk."""
        assert Question._mask_token("a") == "*"

    def test_mask_token_two(self) -> None:
        """Keep the first character and mask the second for two-character tokens."""
        assert Question._mask_token("ab") == "a*"

    def test_mask_token_long(self) -> None:
        """Preserve first and last characters with asterisks in between."""
        assert Question._mask_token("john") == "j***n"

    def test_has_answer_true(self) -> None:
        """Return True when the question has at least one answer."""
        q = baker.make(Question)
        baker.make(Answer, question=q)
        assert q.has_answer is True

    def test_has_answer_false(self) -> None:
        """Return False when the question has no answers."""
        q = baker.make(Question)
        assert q.has_answer is False

    def test_vote_count_annotated(self) -> None:
        """Use the annotated votes_count field when the QuerySet provides it."""
        talk = baker.make(Talk)
        q = baker.make(Question, talk=talk)
        user = baker.make(CustomUser)
        baker.make(QuestionVote, question=q, user=user)
        q_annotated = Question.objects.filter(pk=q.pk).with_vote_count().first()
        assert q_annotated is not None
        assert q_annotated.vote_count == 1

    def test_vote_count_dynamic(self) -> None:
        """Dynamically count votes via a database query when no annotation exists."""
        q = baker.make(Question)
        user = baker.make(CustomUser)
        baker.make(QuestionVote, question=q, user=user)
        assert q.vote_count == 1

    def test_user_has_voted_true(self) -> None:
        """Return True when the user has already voted on this question."""
        q = baker.make(Question)
        user = baker.make(CustomUser)
        baker.make(QuestionVote, question=q, user=user)
        assert q.user_has_voted(user) is True

    def test_user_has_voted_false(self) -> None:
        """Return False when the user has not voted on this question."""
        q = baker.make(Question)
        user = baker.make(CustomUser)
        assert q.user_has_voted(user) is False

    def test_user_has_voted_none(self) -> None:
        """Return False when the user argument is None (anonymous)."""
        q = baker.make(Question)
        assert q.user_has_voted(None) is False

    def test_mark_as_answered(self) -> None:
        """Transition an approved question to ANSWERED status."""
        q = baker.make(Question, status=Question.Status.APPROVED)
        q.mark_as_answered()
        q.refresh_from_db()
        assert q.status == Question.Status.ANSWERED

    def test_reject(self) -> None:
        """Transition an approved question to REJECTED status."""
        q = baker.make(Question, status=Question.Status.APPROVED)
        q.reject()
        q.refresh_from_db()
        assert q.status == Question.Status.REJECTED

    def test_approve(self) -> None:
        """Transition a rejected question back to APPROVED status."""
        q = baker.make(Question, status=Question.Status.REJECTED)
        q.approve()
        q.refresh_from_db()
        assert q.status == Question.Status.APPROVED


@pytest.mark.django_db
class TestQuestionVoteModel:
    """Tests for the QuestionVote model."""

    def test_str(self) -> None:
        """Include the voter email and question PK in the string representation."""
        user = baker.make(CustomUser, email="voter@example.com")
        q = baker.make(Question)
        vote = baker.make(QuestionVote, question=q, user=user)
        assert "voter@example.com" in str(vote)
        assert str(q.pk) in str(vote)


@pytest.mark.django_db
class TestAnswerModel:
    """Tests for the Answer model."""

    def test_str_short(self) -> None:
        """Return the full content for short answers."""
        a = baker.make(Answer, content="Short answer")
        assert str(a) == "Short answer"

    def test_str_long(self) -> None:
        """Truncate long answer content with an ellipsis."""
        a = baker.make(Answer, content="a" * 100)
        assert str(a).endswith("...")

    def test_save_updates_question_status(self) -> None:
        """Automatically mark the parent question as ANSWERED when saving an answer."""
        q = baker.make(Question, status=Question.Status.APPROVED)
        baker.make(Answer, question=q)
        q.refresh_from_db()
        assert q.status == Question.Status.ANSWERED

    def test_save_does_not_update_rejected_question(self) -> None:
        """Do not change a REJECTED question's status when an answer is saved."""
        q = baker.make(Question, status=Question.Status.REJECTED)
        baker.make(Answer, question=q)
        q.refresh_from_db()
        assert q.status == Question.Status.REJECTED
