"""
Admin configuration for Q&A models (Question, QuestionVote, Answer).

Split out from ``talks.admin`` so the Q&A admin classes live next to each other without pulling the
core Talk/Room/Speaker admins along.
"""

from typing import TYPE_CHECKING, Any, ClassVar

from django.contrib import admin
from django.db.models import Count, Exists, OuterRef
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from .models_qa import Answer, Question, QuestionVote


if TYPE_CHECKING:
    from django.db.models import QuerySet
    from django.http import HttpRequest
    from django.utils.functional import _StrPromise as StrOrPromise


class AnswerInline(admin.TabularInline[Answer, Question]):
    """Inline admin for Answer model."""

    model = Answer
    extra = 1
    fields = ("content", "user", "is_official", "created_at")
    readonly_fields = ("created_at",)


@admin.register(Question)
class QuestionAdmin(admin.ModelAdmin[Question]):
    """
    Admin configuration for the Question model.

    Attributes:
        list_display: Fields to display in the admin list view.
        list_filter: Fields to filter by in the admin list view.
        search_fields: Fields to search by in the admin list view.
        actions: Custom admin actions.
        readonly_fields: Fields that cannot be edited in the admin.
        inlines: Related models to display inline.
        list_select_related: Related models to prefetch for list view optimization.

    """

    list_display = (
        "content_preview",
        "talk",
        "display_name",
        "vote_count",
        "status",
        "has_answers",
        "created_at",
    )
    list_filter = ("status", "created_at", "talk__title")
    search_fields = ("content", "user__email", "user__first_name", "user__last_name", "talk__title")
    actions = ("approve_questions", "reject_questions", "mark_as_answered")
    readonly_fields = ("vote_count", "created_at", "updated_at")
    inlines = (AnswerInline,)
    list_select_related = ("talk", "user")

    def get_queryset(self, request: HttpRequest) -> QuerySet[Question]:
        """Annotate queryset with vote count and answer existence to avoid N+1 queries."""
        qs = super().get_queryset(request)
        return qs.annotate(
            votes_count=Count("votes"),
            _has_answers=Exists(Answer.objects.filter(question=OuterRef("pk"))),
        )

    fieldsets: ClassVar[list[Any]] = [
        (
            None,
            {
                "fields": ("talk", "content", "status"),
            },
        ),
        (
            _("Author"),
            {
                "fields": ("user",),
            },
        ),
        (
            _("Metadata"),
            {
                "fields": ("vote_count", "created_at", "updated_at"),
                "classes": ("collapse",),
            },
        ),
    ]

    @admin.display(description=_("Question"))
    def content_preview(self, obj: Question) -> str:
        """Display a preview of the question content."""
        return str(obj)

    @admin.display(boolean=True, description=_("Has Answers"))
    def has_answers(self, obj: Question) -> bool:
        """Display whether the question has answers."""
        if hasattr(obj, "_has_answers"):
            return bool(obj._has_answers)  # noqa: SLF001
        return obj.has_answer

    @admin.display(description=_("Votes"))
    def vote_count(self, obj: Question) -> int:
        """Display the number of votes for this question."""
        return obj.vote_count

    def _bulk_set_status(
        self,
        request: HttpRequest,
        queryset: QuerySet[Question],
        status: Question.Status,
        message_template: StrOrPromise,
    ) -> None:
        """Update *queryset*'s status and notify the admin user."""
        updated = queryset.update(status=status, updated_at=timezone.now())
        self.message_user(request, message_template % {"count": updated})

    @admin.action(description=_("Reject selected questions (hide from public)"))
    def reject_questions(self, request: HttpRequest, queryset: QuerySet[Question]) -> None:
        """Mark selected questions as rejected, hiding them from public view."""
        self._bulk_set_status(
            request,
            queryset,
            Question.Status.REJECTED,
            _("%(count)d question(s) have been rejected."),
        )

    @admin.action(description=_("Mark selected questions as answered"))
    def mark_as_answered(self, request: HttpRequest, queryset: QuerySet[Question]) -> None:
        """Mark selected questions as answered."""
        self._bulk_set_status(
            request,
            queryset,
            Question.Status.ANSWERED,
            _("%(count)d question(s) have been marked as answered."),
        )

    @admin.action(description=_("Approve selected questions"))
    def approve_questions(self, request: HttpRequest, queryset: QuerySet[Question]) -> None:
        """Mark selected questions as approved."""
        self._bulk_set_status(
            request,
            queryset,
            Question.Status.APPROVED,
            _("%(count)d question(s) have been approved."),
        )


@admin.register(QuestionVote)
class QuestionVoteAdmin(admin.ModelAdmin[QuestionVote]):
    """
    Admin configuration for the QuestionVote model.

    Attributes:
        list_display: Fields to display in the admin list view.
        list_filter: Fields to filter by in the admin list view.
        search_fields: Fields to search by in the admin list view.
        readonly_fields: Fields that cannot be edited in the admin.

    """

    list_display = ("question_preview", "user", "created_at")
    list_filter = ("created_at",)
    search_fields = ("question__content", "user__email")
    readonly_fields = ("created_at",)

    @admin.display(description=_("Question"))
    def question_preview(self, obj: QuestionVote) -> str:
        """Display a preview of the question content."""
        return str(obj.question)


@admin.register(Answer)
class AnswerAdmin(admin.ModelAdmin[Answer]):
    """
    Admin configuration for the Answer model.

    Attributes:
        list_display: Fields to display in the admin list view.
        list_filter: Fields to filter by in the admin list view.
        search_fields: Fields to search by in the admin list view.
        readonly_fields: Fields that cannot be edited in the admin.
        list_select_related: Related models to prefetch for list view optimization.

    """

    list_display = ("content_preview", "question_preview", "user", "is_official", "created_at")
    list_filter = ("is_official", "created_at")
    search_fields = ("content", "question__content", "user__email")
    readonly_fields = ("created_at", "updated_at")
    list_select_related = ("question", "user")

    fieldsets: ClassVar[list[Any]] = [
        (
            None,
            {
                "fields": ("question", "content", "user", "is_official"),
            },
        ),
        (
            _("Metadata"),
            {
                "fields": ("created_at", "updated_at"),
                "classes": ("collapse",),
            },
        ),
    ]

    @admin.display(description=_("Answer"))
    def content_preview(self, obj: Answer) -> str:
        """Display a preview of the answer content."""
        return str(obj)

    @admin.display(description=_("Question"))
    def question_preview(self, obj: Answer) -> str:
        """Display a preview of the question content."""
        return str(obj.question)
