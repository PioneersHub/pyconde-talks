"""
Views for managing and displaying Question and Answer objects.

This module provides class-based and function-based views for handling Question and Answer including
listing, creating, voting, and moderation actions.
"""

from typing import Any, ClassVar, Protocol

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.db.models import Q
from django.db.models.query import QuerySet
from django.http import Http404, HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_POST
from django.views.generic import CreateView, ListView, UpdateView

from .models import Talk
from .models_qa import Question, QuestionVote
from .utils import get_talk_by_id_or_pretalx


class QuestionListView(LoginRequiredMixin, ListView):
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
        if self.request.headers.get("HX-Request"):
            return [self.fragment_template]
        return [self.template_name]

    def get_queryset(self) -> QuerySet[Question]:
        """Get questions for the specific talk, sorted by votes."""
        self.talk = get_object_or_404(Talk, pk=self.kwargs["talk_id"])

        # Get the status filter from the request
        self.status_filter = self.request.GET.get("status_filter", "all")

        # Use the shared function to get filtered questions
        return get_filtered_questions(self.request, self.talk, self.status_filter)

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        """Enhance the template context with additional data."""
        context = super().get_context_data(**kwargs)
        built = build_question_list_context(self.request, self.talk, self.status_filter)
        context.update(built)
        return context


class QuestionCreateView(LoginRequiredMixin, CreateView):
    """
    Create a new question for a talk.

    Requires login to create questions.
    """

    model = Question
    template_name = "talks/questions/question_form.html"
    fields: ClassVar[list[str]] = ["content"]

    def form_valid(self, form: forms.ModelForm) -> HttpResponse:
        """Process the form submission."""
        # Set the talk and user
        form.instance.talk = get_object_or_404(Talk, pk=self.kwargs["talk_id"])
        form.instance.user = self.request.user

        # All questions are now approved by default
        # This happens automatically due to the model default

        # Save the question
        response = super().form_valid(form)

        # Show success message
        messages.success(self.request, _("Your question has been posted."))

        # If this is an HTMX request, return to the question list
        if self.request.headers.get("HX-Request"):
            user_can_moderate = is_moderator(self.request.user)
            status_filter = self.request.GET.get("status_filter", "all")
            return render(
                self.request,
                "talks/questions/question_success.html",
                {
                    "question": form.instance,
                    "user_can_moderate": user_can_moderate,
                    "status_filter": status_filter,
                },
            )

        return response

    def get_success_url(self) -> str:
        """Redirect to the talk's Q&A page."""
        return reverse("talk_questions", args=[self.kwargs["talk_id"]])


def get_filtered_questions(
    request: HttpRequest,
    talk: Talk,
    status_filter: str = "all",
) -> QuerySet:
    """
    Get filtered questions based on user permissions and filter selection.

    This function centralizes the filtering logic used in both QuestionListView and vote_question.
    """
    # Get base queryset of questions for this talk
    queryset = Question.objects.filter(talk=talk)

    # Filter for user's own questions
    if status_filter == "mine":
        return queryset.filter(user=request.user).sorted_by_votes()

    # Apply filtering based on user permissions and filter selection
    is_moderator = request.user.is_staff or request.user.is_superuser

    # For moderators, respect the filter if provided
    if is_moderator:
        if status_filter == "approved":
            queryset = queryset.filter(status=Question.Status.APPROVED)
        elif status_filter == "answered":
            queryset = queryset.filter(status=Question.Status.ANSWERED)
        elif status_filter == "rejected":
            queryset = queryset.filter(status=Question.Status.REJECTED)
        # "all" doesn't need filtering as it shows everything
    # Regular users can see approved, answered, and their own rejected questions
    elif status_filter == "approved":
        queryset = queryset.filter(status=Question.Status.APPROVED)
    elif status_filter == "answered":
        queryset = queryset.filter(status=Question.Status.ANSWERED)
    else:
        # Default for regular users: show approved and answered, plus their own rejected questions
        queryset = queryset.filter(
            Q(status__in=[Question.Status.APPROVED, Question.Status.ANSWERED])
            | Q(status=Question.Status.REJECTED, user=request.user),
        )

    return queryset.sorted_by_votes()


def build_question_list_context(
    request: HttpRequest,
    talk: Talk,
    status_filter: str,
) -> dict:
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
            q.user_voted = q.id in user_voted_questions

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


@login_required
def vote_question(request: HttpRequest, question_id: int) -> HttpResponse:
    """
    Handle voting for a question.

    If the user has already voted, the vote is removed (toggle behavior).
    Returns HTML for HTMX to replace the voting div.
    """
    question = get_object_or_404(Question, pk=question_id)

    # Check if user has already voted
    existing_vote = QuestionVote.objects.filter(
        question=question,
        user=request.user,
    ).first()

    if existing_vote:
        # Remove vote if it exists
        existing_vote.delete()
        question.user_voted = False
    else:
        # Create new vote
        QuestionVote.objects.create(
            question=question,
            user=request.user,
        )
        question.user_voted = True

    # Return HTML for HTMX to replace the question list with sorted questions
    if request.headers.get("HX-Request"):
        talk = question.talk
        status_filter = request.GET.get("status_filter", "all")
        return render_question_list_fragment(request, talk, status_filter)

    # Fallback to JSON response for non-HTMX requests
    return JsonResponse(
        {
            "vote_count": question.votes.count(),
            "user_voted": question.user_voted,
        },
    )


@login_required
@require_POST
def delete_question(request: HttpRequest, question_id: int) -> HttpResponse:
    """Allow a user to delete their own question."""
    question = get_object_or_404(Question, pk=question_id)
    if question.user != request.user and not is_moderator(request.user):
        return HttpResponse(status=403)
    talk = question.talk
    question.delete()
    messages.success(request, _("Your question has been deleted."))
    if request.headers.get("HX-Request"):
        status_filter = request.GET.get("status_filter", "all")
        return render_question_list_fragment(request, talk, status_filter)
    return redirect("talk_questions", talk_id=talk.id)


class QuestionOwnerRequiredMixin(UserPassesTestMixin):
    """Mixin to require that the current user owns the question."""

    def test_func(self) -> bool:
        """Return True if the current user is the owner of the target question."""
        question_id = self.kwargs.get("question_id")
        question = get_object_or_404(Question, pk=question_id)
        return question.user == self.request.user


class QuestionUpdateView(LoginRequiredMixin, QuestionOwnerRequiredMixin, UpdateView):
    """Allow a question owner to edit content; clears votes upon successful update."""

    model = Question
    fields: ClassVar[list[str]] = ["content"]
    template_name = "talks/questions/question_edit_form.html"
    pk_url_kwarg = "question_id"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        """Add status filter to context."""
        ctx = super().get_context_data(**kwargs)
        ctx["status_filter"] = (
            self.request.GET.get("status_filter") or self.request.POST.get("status_filter") or "all"
        )
        return ctx

    def form_valid(self, form: forms.ModelForm) -> HttpResponse:
        """Persist changes and clear all existing votes, notifying the user."""
        # Save updated content
        response = super().form_valid(form)
        # Clear all votes after content change
        QuestionVote.objects.filter(question=self.object).delete()
        messages.warning(
            self.request,
            _("Your question was updated and all previous votes were cleared."),
        )
        if self.request.headers.get("HX-Request"):
            talk = form.instance.talk
            status_filter = self.request.GET.get("status_filter", "all")
            return render_question_list_fragment(self.request, talk, status_filter)
        return response

    def get_success_url(self) -> str:
        """Redirect back to the talk's questions list after a successful update."""
        return reverse("talk_questions", args=[self.object.talk_id])


# Moderator views
class UserWithPermissions(Protocol):
    """Protocol for objects with permission attributes."""

    is_staff: bool
    is_superuser: bool


def is_moderator(user: UserWithPermissions) -> bool:
    """Check if the user is a moderator (staff or superuser)."""
    return user.is_staff or user.is_superuser


class ModeratorRequiredMixin(UserPassesTestMixin):
    """Mixin to require moderator permissions."""

    def test_func(self) -> bool:
        """Check if the user is a moderator."""
        return is_moderator(self.request.user)


@require_POST
@user_passes_test(is_moderator)
def reject_question(request: HttpRequest, question_id: int) -> HttpResponse:
    """Reject a question."""
    question = get_object_or_404(Question, pk=question_id)
    question.reject()
    messages.success(request, _("Question has been rejected."))

    if request.headers.get("HX-Request"):
        talk = question.talk
        status_filter = request.GET.get("status_filter", "all")
        return render_question_list_fragment(request, talk, status_filter)

    return redirect("talk_questions", talk_id=question.talk.id)


@require_POST
@user_passes_test(is_moderator)
def mark_question_answered(request: HttpRequest, question_id: int) -> HttpResponse:
    """Mark a question as answered."""
    question = get_object_or_404(Question, pk=question_id)
    question.mark_as_answered()
    messages.success(request, _("Question has been marked as answered."))

    if request.headers.get("HX-Request"):
        talk = question.talk
        status_filter = request.GET.get("status_filter", "all")
        return render_question_list_fragment(request, talk, status_filter)

    return redirect("talk_questions", talk_id=question.talk.id)


@require_POST
@user_passes_test(is_moderator)
def approve_question(request: HttpRequest, question_id: int) -> HttpResponse:
    """Approve a question."""
    question = get_object_or_404(Question, pk=question_id)
    question.approve()
    messages.success(request, _("Question has been approved."))

    if request.headers.get("HX-Request"):
        talk = question.talk
        status_filter = request.GET.get("status_filter", "all")
        return render_question_list_fragment(request, talk, status_filter)

    return redirect("talk_questions", talk_id=question.talk.id)


def question_redirect_view(_: HttpRequest, talk_id: str) -> HttpResponse:
    """Get talk question view by Talk ID or pretalx_id."""
    talk = get_talk_by_id_or_pretalx(talk_id)
    if talk:
        return redirect("talk_questions", talk_id=talk.pk)
    msg = f"No talk found with ID or pretalx ID: {talk_id}"
    raise Http404(msg)
