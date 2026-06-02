"""
Views for managing and displaying Question and Answer objects.

This module provides class-based and function-based views for handling Question and Answer including
listing, creating, voting, and moderation actions.
"""

from typing import TYPE_CHECKING, Any, cast

from django import forms
from django.contrib import messages
from django.contrib.auth.mixins import UserPassesTestMixin
from django.core.exceptions import PermissionDenied
from django.db.models import Q
from django.http import Http404, HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_POST, require_safe
from django.views.generic import CreateView, ListView, UpdateView

from .models import Talk
from .models_qa import Question, QuestionQuerySet, QuestionVote
from .utils import get_talk_by_id_or_pretalx, is_htmx_request


if TYPE_CHECKING:
    from django.contrib.auth.models import AbstractBaseUser, AnonymousUser
    from django.utils.functional import _StrPromise as StrOrPromise

    from users.models import CustomUser


def _get_status_filter(request: HttpRequest) -> str:
    """Return the status_filter from POST (hx-vals) or GET, defaulting to 'all'."""
    return request.POST.get("status_filter") or request.GET.get("status_filter", "all")


def _get_accessible_question(user: AbstractBaseUser | AnonymousUser, question_id: int) -> Question:
    """Return the question if the user has access to its talk's event, or raise Http404."""
    question = get_object_or_404(Question.objects.select_related("talk"), pk=question_id)
    accessible = Talk.objects.accessible_to(cast("CustomUser", user))
    if not accessible.filter(pk=question.talk_id).exists():
        raise Http404
    return question


class QuestionListView(ListView[Question]):
    """
    Display a list of questions for a specific talk.

    Questions are sorted by vote count, with the most popular at the top.
    Only approved, answered and their own questions are shown to regular users.
    Moderators can see all questions including pending ones.
    """

    model = Question
    template_name = "talks/questions/question_list.html"
    context_object_name = "questions"
    fragment_template = f"{template_name}#question-list"

    def get_template_names(self) -> list[str]:
        """
        Determine which template to use.

        Return a partial fragment for HTMX requests.
        """
        if is_htmx_request(self.request):
            return [self.fragment_template]
        return [cast("str", self.template_name)]  # type: ignore[redundant-cast]

    def get_queryset(self) -> QuestionQuerySet:
        """Get questions for the specific talk, sorted by votes."""
        user = cast("CustomUser", self.request.user)
        self.talk = get_object_or_404(Talk.objects.accessible_to(user), pk=self.kwargs["talk_id"])

        # Get the status filter from the request
        self.status_filter = self.request.GET.get("status_filter", "all")

        # Use the shared function to get filtered questions
        return get_filtered_questions(
            self.request,
            self.talk,
            self.status_filter,
        )

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        """Enhance the template context with additional data."""
        context = super().get_context_data(**kwargs)

        # Annotate user_voted on the already-fetched object_list instead of re-querying
        questions = context["questions"]
        if self.request.user.is_authenticated:
            user_voted_questions = set(
                QuestionVote.objects.filter(
                    user=self.request.user,
                    question__talk=self.talk,
                ).values_list("question_id", flat=True),
            )
            for q in questions:
                q.user_voted = q.pk in user_voted_questions

        context["talk"] = self.talk
        context["user_can_moderate"] = is_moderator(self.request.user)
        context["status_filter"] = self.status_filter
        return context


class QuestionCreateView(CreateView[Question, forms.ModelForm[Question]]):
    """
    Create a new question for a talk.

    Requires login to create questions.
    """

    model = Question
    template_name = "talks/questions/question_form.html"
    fields = ("content",)

    def form_valid(self, form: forms.ModelForm[Question]) -> HttpResponse:
        """Process the form submission."""
        question: Question = form.instance

        # Set the talk and user
        user = cast("CustomUser", self.request.user)
        question.talk = get_object_or_404(
            Talk.objects.accessible_to(user),
            pk=self.kwargs["talk_id"],
        )
        question.user = self.request.user

        # Save the question
        response = super().form_valid(form)

        # Auto vote your own question
        QuestionVote.objects.get_or_create(
            question=question,
            user=self.request.user,
        )

        # Show success message
        messages.success(self.request, _("Your question has been posted."))

        # If this is an HTMX request, return to the question list
        if is_htmx_request(self.request):
            user_can_moderate = is_moderator(self.request.user)
            status_filter = self.request.GET.get("status_filter", "all")
            return render(
                self.request,
                "talks/questions/question_success.html",
                {
                    "question": question,
                    "user_can_moderate": user_can_moderate,
                    "status_filter": status_filter,
                },
            )

        return response

    def get_success_url(self) -> str:
        """Redirect to the talk's Q&A page."""
        return reverse("talk_questions", args=[self.kwargs["talk_id"]])


_STATUS_Q: dict[str, Q] = {
    "approved": Q(status=Question.Status.APPROVED),
    "answered": Q(status=Question.Status.ANSWERED),
    "rejected": Q(status=Question.Status.REJECTED),
}


def get_filtered_questions(
    request: HttpRequest,
    talk: Talk,
    status_filter: str = "all",
) -> QuestionQuerySet:
    """
    Get filtered questions based on user permissions and filter selection.

    This function centralizes the filtering logic used in both QuestionListView and vote_question.
    """
    queryset = Question.objects.filter(talk=talk).select_related("user")

    if status_filter == "mine":
        return queryset.filter(user=request.user).sorted_by_votes()

    # "approved" and "answered" work the same for everyone
    if status_filter in ("approved", "answered"):
        return queryset.filter(_STATUS_Q[status_filter]).sorted_by_votes()

    # Moderators can view rejected questions and unfiltered "all"
    if is_moderator(request.user):
        if status_filter == "rejected":
            return queryset.filter(_STATUS_Q["rejected"]).sorted_by_votes()
        return queryset.sorted_by_votes()

    # Default for regular users: approved + answered, plus their own rejected questions
    return queryset.filter(
        Q(status__in=[Question.Status.APPROVED, Question.Status.ANSWERED])
        | Q(status=Question.Status.REJECTED, user=request.user),
    ).sorted_by_votes()


def build_question_list_context(
    request: HttpRequest,
    talk: Talk,
    status_filter: str,
) -> dict[str, Any]:
    """Build context for rendering the question list partial."""
    questions = get_filtered_questions(request, talk, status_filter)

    if request.user.is_authenticated:
        user_voted_questions = set(
            QuestionVote.objects.filter(
                user=request.user,
                question__talk=talk,
            ).values_list("question_id", flat=True),
        )
        for q in questions:
            q.user_voted = q.pk in user_voted_questions

    return {
        "questions": questions,
        "talk": talk,
        "user_can_moderate": is_moderator(request.user),
        "status_filter": status_filter,
    }


def render_question_list_fragment(
    request: HttpRequest,
    talk: Talk,
    status_filter: str,
) -> HttpResponse:
    """Render the question list partial via the template loader fragment path."""
    ctx = build_question_list_context(request, talk, status_filter)
    return render(
        request,
        "talks/questions/question_list.html#question-list",
        ctx,
    )


@require_POST
def vote_question(request: HttpRequest, question_id: int) -> HttpResponse:
    """
    Handle voting for a question.

    If the user has already voted, the vote is removed (toggle behavior).
    Returns HTML for HTMX to replace the voting div.
    """
    question = _get_accessible_question(request.user, question_id)

    # Atomic toggle: rely on the (question, user) unique constraint so two concurrent
    # clicks can't both insert a vote (which previously caused an IntegrityError 500).
    vote, created = QuestionVote.objects.get_or_create(
        question=question,
        user=request.user,
    )
    if not created:
        vote.delete()
    question.user_voted = created

    # Return HTML for HTMX to replace the question list with sorted questions
    if is_htmx_request(request):
        talk = question.talk
        return render_question_list_fragment(request, talk, _get_status_filter(request))

    # Fallback to JSON response for non-HTMX requests
    return JsonResponse(
        {
            "vote_count": question.votes.count(),
            "user_voted": question.user_voted,
        },
    )


@require_POST
def delete_question(request: HttpRequest, question_id: int) -> HttpResponse:
    """Allow a user to delete their own question."""
    question = _get_accessible_question(request.user, question_id)
    if question.user != request.user and not is_moderator(request.user):
        raise PermissionDenied
    talk = question.talk
    question.delete()
    messages.success(request, _("Your question has been deleted."))
    if is_htmx_request(request):
        return render_question_list_fragment(request, talk, _get_status_filter(request))
    return redirect("talk_questions", talk_id=talk.pk)


class QuestionOwnerRequiredMixin(UserPassesTestMixin):
    """Mixin to require that the current user owns the question."""

    request: HttpRequest
    kwargs: dict[str, Any]

    def test_func(self) -> bool:
        """Return True if the current user is the owner of the target question."""
        question_id = self.kwargs.get("question_id")
        return Question.objects.filter(pk=question_id, user=self.request.user).exists()


class QuestionUpdateView(
    QuestionOwnerRequiredMixin,
    UpdateView[Question, forms.ModelForm[Question]],
):
    """Allow a question owner to edit content; clears votes upon successful update."""

    model = Question
    fields = ("content",)
    template_name = "talks/questions/question_edit_form.html"
    pk_url_kwarg = "question_id"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        """Add status filter to context."""
        ctx = super().get_context_data(**kwargs)
        ctx["status_filter"] = _get_status_filter(self.request)
        return ctx

    def form_valid(
        self,
        form: forms.ModelForm[Question],
    ) -> HttpResponse:
        """Persist changes and clear all existing votes, notifying the user."""
        # Save updated content
        response = super().form_valid(form)
        # Clear all votes (except your own) after content change
        QuestionVote.objects.filter(question=self.object).exclude(user=self.request.user).delete()
        messages.warning(
            self.request,
            _("Your question was updated and all previous votes were cleared."),
        )
        if is_htmx_request(self.request):
            talk = form.instance.talk
            return render_question_list_fragment(
                self.request,
                talk,
                _get_status_filter(self.request),
            )
        return response

    def get_success_url(self) -> str:
        """Redirect back to the talk's questions list after a successful update."""
        return reverse("talk_questions", args=[self.object.talk.pk])


# Moderator views
def is_moderator(user: AbstractBaseUser | AnonymousUser) -> bool:
    """Check if the user is a moderator (staff or superuser)."""
    if not getattr(user, "is_authenticated", False):
        return False
    return getattr(user, "is_staff", False) or getattr(user, "is_superuser", False)


class ModeratorRequiredMixin(UserPassesTestMixin):  # pragma: no cover
    """Mixin to require moderator permissions."""

    request: HttpRequest

    def test_func(self) -> bool:
        """Check if the user is a moderator."""
        return is_moderator(self.request.user)


def _moderate_question(
    request: HttpRequest,
    question_id: int,
    action: str,
    success_message: StrOrPromise,
) -> HttpResponse:
    """Apply a moderator-only state change to a question and respond."""
    if not is_moderator(request.user):
        raise PermissionDenied
    question = _get_accessible_question(request.user, question_id)
    getattr(question, action)()
    messages.success(request, success_message)

    if is_htmx_request(request):
        return render_question_list_fragment(
            request,
            question.talk,
            _get_status_filter(request),
        )
    return redirect("talk_questions", talk_id=question.talk.pk)


@require_POST
def reject_question(request: HttpRequest, question_id: int) -> HttpResponse:
    """Reject a question."""
    return _moderate_question(
        request,
        question_id,
        "reject",
        _("Question has been rejected."),
    )


@require_POST
def mark_question_answered(request: HttpRequest, question_id: int) -> HttpResponse:
    """Mark a question as answered."""
    return _moderate_question(
        request,
        question_id,
        "mark_as_answered",
        _("Question has been marked as answered."),
    )


@require_POST
def approve_question(request: HttpRequest, question_id: int) -> HttpResponse:
    """Approve a question."""
    return _moderate_question(
        request,
        question_id,
        "approve",
        _("Question has been approved."),
    )


@require_safe
def question_redirect_view(request: HttpRequest, talk_id: str) -> HttpResponse:
    """Get talk question view by Talk ID or pretalx_id."""
    talk = get_talk_by_id_or_pretalx(talk_id, user=cast("CustomUser", request.user))
    if talk:
        return redirect("talk_questions", talk_id=talk.pk)
    msg = f"No talk found with ID or pretalx ID: {talk_id}"
    raise Http404(msg)
