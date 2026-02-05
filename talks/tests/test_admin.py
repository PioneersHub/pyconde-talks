"""Tests for talks.admin covering all admin classes and actions."""
# ruff: noqa: PLC0415 PLR2004 SLF001

from datetime import timedelta

import pytest
from django.contrib.admin.sites import AdminSite
from django.test import RequestFactory
from django.utils import timezone
from model_bakery import baker

from talks.admin import (
    AnswerAdmin,
    QuestionAdmin,
    QuestionVoteAdmin,
    RoomAdmin,
    SpeakerAdmin,
    StreamingAdmin,
    TalkAdmin,
)
from talks.models import Room, Speaker, Streaming, Talk
from talks.models_qa import Answer, Question, QuestionVote
from users.models import CustomUser


site = AdminSite()


@pytest.fixture()
def rf() -> RequestFactory:
    """Return a Django RequestFactory for building test requests."""
    return RequestFactory()


@pytest.fixture()
def admin_user() -> CustomUser:
    """Return a superuser required to access admin views."""
    return CustomUser.objects.create_superuser(
        email="admin@admin.com",
        password="admin123!",
    )


# ---------------------------------------------------------------------------
# RoomAdmin
# ---------------------------------------------------------------------------
@pytest.mark.django_db
class TestRoomAdmin:
    """Verify RoomAdmin list display helpers and computed columns."""

    def test_talk_count(self, rf: RequestFactory, admin_user: CustomUser) -> None:
        """Annotated talk_count column returns the number of talks in a room."""
        room = baker.make(Room, name="Test Room")
        baker.make(Talk, room=room, _quantity=3)
        admin = RoomAdmin(Room, site)
        request = rf.get("/")
        request.user = admin_user
        qs = admin.get_queryset(request)
        room_obj = qs.get(pk=room.pk)
        assert admin.talk_count(room_obj) == 3

    def test_streaming_count(self, rf: RequestFactory, admin_user: CustomUser) -> None:
        """Annotated streaming_count column returns the number of streamings in a room."""
        room = baker.make(Room, name="Stream Room")
        now = timezone.now()
        for i in range(2):
            baker.make(
                Streaming,
                room=room,
                start_time=now + timedelta(days=i, hours=1),
                end_time=now + timedelta(days=i, hours=5),
                video_link=f"https://youtube.com/live{i}",
            )
        admin = RoomAdmin(Room, site)
        request = rf.get("/")
        request.user = admin_user
        qs = admin.get_queryset(request)
        room_obj = qs.get(pk=room.pk)
        assert admin.streaming_count(room_obj) == 2

    def test_has_slido_link(self) -> None:
        """Boolean column returns True when the room has a Slido link, False otherwise."""
        admin = RoomAdmin(Room, site)
        room_yes = baker.prepare(Room, slido_link="https://slido.com/123")
        room_no = baker.prepare(Room, slido_link="")
        assert admin.has_slido_link(room_yes) is True
        assert admin.has_slido_link(room_no) is False


# ---------------------------------------------------------------------------
# StreamingAdmin
# ---------------------------------------------------------------------------
@pytest.mark.django_db
class TestStreamingAdmin:
    """Verify StreamingAdmin display helpers for video links."""

    def test_formatted_video_link(self) -> None:
        """Render the video link as a clickable HTML anchor tag."""
        admin = StreamingAdmin(Streaming, site)
        streaming = baker.make(
            Streaming,
            video_link="https://youtube.com/live",
            start_time=timezone.now(),
            end_time=timezone.now() + timedelta(hours=1),
        )
        result = admin.formatted_video_link(streaming)
        assert "youtube.com" in result
        assert "<a " in result

    def test_formatted_video_link_empty(self) -> None:
        """Return a dash placeholder when no video link is set."""
        admin = StreamingAdmin(Streaming, site)
        streaming = baker.make(
            Streaming,
            video_link="",
            start_time=timezone.now(),
            end_time=timezone.now() + timedelta(hours=1),
        )
        assert admin.formatted_video_link(streaming) == "-"


# ---------------------------------------------------------------------------
# SpeakerAdmin
# ---------------------------------------------------------------------------
@pytest.mark.django_db
class TestSpeakerAdmin:
    """Verify SpeakerAdmin display helpers for avatar and talk count."""

    def test_display_avatar(self) -> None:
        """Render the speaker avatar as an HTML img tag when a URL is set."""
        admin = SpeakerAdmin(Speaker, site)
        speaker = baker.make(Speaker, avatar="https://example.com/avatar.jpg")
        result = admin.display_avatar(speaker)
        assert "img" in result.lower()

    def test_display_avatar_empty(self) -> None:
        """Return a dash placeholder when the speaker has no avatar URL."""
        admin = SpeakerAdmin(Speaker, site)
        speaker = baker.make(Speaker, avatar="")
        assert admin.display_avatar(speaker) == "-"

    def test_talk_count(self, rf: RequestFactory, admin_user: CustomUser) -> None:
        """Annotated talk_count returns the number of talks for a speaker."""
        admin = SpeakerAdmin(Speaker, site)
        speaker = baker.make(Speaker)
        talk = baker.make(Talk)
        talk.speakers.add(speaker)
        request = rf.get("/")
        request.user = admin_user
        qs = admin.get_queryset(request)
        speaker_obj = qs.get(pk=speaker.pk)
        assert admin.talk_count(speaker_obj) == 1


# ---------------------------------------------------------------------------
# TalkAdmin
# ---------------------------------------------------------------------------
@pytest.mark.django_db
class TestTalkAdmin:
    """Verify TalkAdmin list display columns, image preview, and streaming info."""

    def test_get_queryset(self, rf: RequestFactory) -> None:
        """Queryset includes prefetched speakers and room for efficient display."""
        admin = TalkAdmin(Talk, site)
        baker.make(Talk)
        request = rf.get("/")
        request.user = baker.make(CustomUser, is_superuser=True, is_staff=True)
        qs = admin.get_queryset(request)
        assert qs.count() > 0

    def test_room_name(self) -> None:
        """Display the associated room name for a talk."""
        admin = TalkAdmin(Talk, site)
        room = baker.make(Room, name="Main Hall")
        talk = baker.make(Talk, room=room)
        assert admin.room_name(talk) == "Main Hall"

    def test_room_name_none(self) -> None:
        """Return an empty string when the talk has no room assigned."""
        admin = TalkAdmin(Talk, site)
        talk = baker.make(Talk, room=None)
        assert admin.room_name(talk) == ""

    def test_display_image_preview_image(self) -> None:
        """Render an img tag when the talk has an uploaded image."""
        admin = TalkAdmin(Talk, site)
        talk = baker.make(Talk, image="talk_images/test.jpg")
        result = admin.display_image_preview(talk)
        assert "img" in result.lower()

    def test_display_image_preview_external(self) -> None:
        """Render an img tag when the talk has an external image URL instead of an upload."""
        admin = TalkAdmin(Talk, site)
        talk = baker.make(Talk, image="", external_image_url="https://example.com/img.jpg")
        result = admin.display_image_preview(talk)
        assert "img" in result.lower()

    def test_display_image_preview_none(self) -> None:
        """Return a dash placeholder when the talk has no image at all."""
        admin = TalkAdmin(Talk, site)
        talk = baker.make(Talk, image="", external_image_url="")
        assert admin.display_image_preview(talk) == "-"

    def test_is_upcoming(self) -> None:
        """Boolean column returns True for talks scheduled in the future."""
        admin = TalkAdmin(Talk, site)
        talk = baker.make(
            Talk,
            start_time=timezone.now() + timedelta(days=1),
            duration=timedelta(minutes=30),
        )
        assert admin.is_upcoming(talk) is True

    def test_has_video(self) -> None:
        """Boolean column reflects whether a video link is set on the talk."""
        admin = TalkAdmin(Talk, site)
        talk = baker.make(
            Talk,
            video_link="https://youtube.com/watch?v=abc",
            start_time=timezone.now() - timedelta(hours=2),
            duration=timedelta(minutes=30),
        )
        assert admin.has_video(talk) is True
        talk2 = baker.make(
            Talk,
            video_link="",
            room=None,
            start_time=timezone.now() - timedelta(hours=2),
            duration=timedelta(minutes=30),
        )
        assert admin.has_video(talk2) is False

    def test_display_active_streaming_no_room(self) -> None:
        """Show 'No room' message when the talk has no room assigned."""
        admin = TalkAdmin(Talk, site)
        talk = baker.make(Talk, room=None)
        result = str(admin.display_active_streaming(talk))
        assert "No room" in result

    def test_display_active_streaming_with_streaming(self) -> None:
        """Show the active streaming link when a live stream covers the talk's time slot."""
        admin = TalkAdmin(Talk, site)
        room = baker.make(Room)
        now = timezone.now()
        baker.make(
            Streaming,
            room=room,
            start_time=now - timedelta(hours=1),
            end_time=now + timedelta(hours=2),
            video_link="https://youtube.com/live",
        )
        talk = baker.make(Talk, room=room, start_time=now, duration=timedelta(minutes=30))
        result = str(admin.display_active_streaming(talk))
        assert "youtube.com" in result

    def test_display_active_streaming_no_streaming(self) -> None:
        """Show 'No active streaming' when the room has no live stream at talk time."""
        admin = TalkAdmin(Talk, site)
        room = baker.make(Room)
        talk = baker.make(
            Talk,
            room=room,
            start_time=timezone.now() + timedelta(days=30),
            duration=timedelta(minutes=30),
        )
        result = str(admin.display_active_streaming(talk))
        assert "No active streaming" in result


# ---------------------------------------------------------------------------
# QuestionAdmin
# ---------------------------------------------------------------------------
@pytest.mark.django_db
class TestQuestionAdmin:
    """Verify QuestionAdmin display columns and bulk moderation actions."""

    def test_content_preview_short(self) -> None:
        """Return the full content when it fits within the truncation limit."""
        admin = QuestionAdmin(Question, site)
        q = baker.make(Question, content="Short")
        assert admin.content_preview(q) == "Short"

    def test_content_preview_long(self) -> None:
        """Truncate long content with an ellipsis to keep the list view readable."""
        admin = QuestionAdmin(Question, site)
        q = baker.make(Question, content="x" * 100)
        assert admin.content_preview(q).endswith("...")

    def test_has_answers(self) -> None:
        """Boolean column reflects whether the question has at least one answer."""
        admin = QuestionAdmin(Question, site)
        q = baker.make(Question)
        assert admin.has_answers(q) is False
        baker.make(Answer, question=q)
        assert admin.has_answers(q) is True

    def test_vote_count_display(self) -> None:
        """Vote count column returns zero for a question with no votes."""
        admin = QuestionAdmin(Question, site)
        q = baker.make(Question)
        assert admin.vote_count(q) == 0

    def test_reject_questions_action(self, rf: RequestFactory, admin_user: CustomUser) -> None:
        """Bulk reject action sets selected questions to REJECTED status."""
        admin = QuestionAdmin(Question, site)
        q1 = baker.make(Question, status=Question.Status.APPROVED)
        q2 = baker.make(Question, status=Question.Status.APPROVED)
        request = rf.post("/")
        request.user = admin_user
        from django.contrib.messages.storage.fallback import FallbackStorage

        request.session = "session"  # type: ignore[assignment]
        request._messages = FallbackStorage(request)  # type: ignore[attr-defined]
        admin.reject_questions(request, Question.objects.filter(pk__in=[q1.pk, q2.pk]))
        q1.refresh_from_db()
        q2.refresh_from_db()
        assert q1.status == Question.Status.REJECTED
        assert q2.status == Question.Status.REJECTED

    def test_mark_as_answered_action(self, rf: RequestFactory, admin_user: CustomUser) -> None:
        """Bulk mark-as-answered action sets selected questions to ANSWERED status."""
        admin = QuestionAdmin(Question, site)
        q = baker.make(Question, status=Question.Status.APPROVED)
        request = rf.post("/")
        request.user = admin_user
        from django.contrib.messages.storage.fallback import FallbackStorage

        request.session = "session"  # type: ignore[assignment]
        request._messages = FallbackStorage(request)  # type: ignore[attr-defined]
        admin.mark_as_answered(request, Question.objects.filter(pk=q.pk))
        q.refresh_from_db()
        assert q.status == Question.Status.ANSWERED

    def test_approve_questions_action(self, rf: RequestFactory, admin_user: CustomUser) -> None:
        """Bulk approve action sets previously rejected questions back to APPROVED."""
        admin = QuestionAdmin(Question, site)
        q = baker.make(Question, status=Question.Status.REJECTED)
        request = rf.post("/")
        request.user = admin_user
        from django.contrib.messages.storage.fallback import FallbackStorage

        request.session = "session"  # type: ignore[assignment]
        request._messages = FallbackStorage(request)  # type: ignore[attr-defined]
        admin.approve_questions(request, Question.objects.filter(pk=q.pk))
        q.refresh_from_db()
        assert q.status == Question.Status.APPROVED


# ---------------------------------------------------------------------------
# QuestionVoteAdmin
# ---------------------------------------------------------------------------
@pytest.mark.django_db
class TestQuestionVoteAdmin:
    """Verify QuestionVoteAdmin preview helpers for the parent question."""

    def test_question_preview_short(self) -> None:
        """Return the full question content when it is short enough."""
        admin = QuestionVoteAdmin(QuestionVote, site)
        q = baker.make(Question, content="Short")
        vote = baker.make(QuestionVote, question=q)
        assert admin.question_preview(vote) == "Short"

    def test_question_preview_long(self) -> None:
        """Truncate the question content with an ellipsis when it is too long."""
        admin = QuestionVoteAdmin(QuestionVote, site)
        q = baker.make(Question, content="x" * 100)
        vote = baker.make(QuestionVote, question=q)
        assert admin.question_preview(vote).endswith("...")


# ---------------------------------------------------------------------------
# AnswerAdmin
# ---------------------------------------------------------------------------
@pytest.mark.django_db
class TestAnswerAdmin:
    """Verify AnswerAdmin preview helpers for both answer and question content."""

    def test_content_preview(self) -> None:
        """Return the full answer content when it fits within the truncation limit."""
        admin = AnswerAdmin(Answer, site)
        a = baker.make(Answer, content="Short answer")
        assert admin.content_preview(a) == "Short answer"

    def test_content_preview_long(self) -> None:
        """Truncate long answer content with an ellipsis for readability."""
        admin = AnswerAdmin(Answer, site)
        a = baker.make(Answer, content="y" * 100)
        assert admin.content_preview(a).endswith("...")

    def test_question_preview(self) -> None:
        """Display the parent question's content from the answer row."""
        admin = AnswerAdmin(Answer, site)
        q = baker.make(Question, content="Question text")
        a = baker.make(Answer, question=q)
        assert admin.question_preview(a) == "Question text"

    def test_question_preview_long(self) -> None:
        """Truncate the parent question content with an ellipsis when too long."""
        admin = AnswerAdmin(Answer, site)
        q = baker.make(Question, content="z" * 100)
        a = baker.make(Answer, question=q)
        assert admin.question_preview(a).endswith("...")
