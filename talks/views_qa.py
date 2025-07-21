"""
Views for managing and displaying Question and Answer objects.

This module provides class-based and function-based views for handling Question and Answer operations,
including listing, creating, voting, and moderation actions.
"""

from typing import Any

from django.contrib import messages
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.db.models.query import QuerySet
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_POST
from django.views.generic import CreateView, ListView, UpdateView

from .models import Talk
from .models_qa import Answer, Question, QuestionVote


class QuestionListView(ListView):
    """
    Display a list of questions for a specific talk.
    
    Questions are sorted by vote count, with the most popular at the top.
    Only approved and answered questions are shown to regular users.
    Moderators can see all questions including pending ones.
    """

    model = Question
    template_name = "talks/questions/question_list.html"
    context_object_name = "questions"
    
    def get_template_names(self) -> list[str]:
        """
        Determine which template to use.
        
        Return a partial fragment for HTMX requests.
        """
        if self.request.headers.get("HX-Request"):
            return ["talks/questions/question_list.html#question-list"]
        return [self.template_name]
    
    def get_queryset(self) -> QuerySet[Question]:
        """Get questions for the specific talk, sorted by votes."""
        self.talk = get_object_or_404(Talk, pk=self.kwargs["talk_id"])
        
        # Get base queryset of questions for this talk
        queryset = Question.objects.filter(talk=self.talk)
        
        # If user is a moderator, show all questions
        if self.request.user.is_staff or self.request.user.is_superuser:
            return queryset.sorted_by_votes()
        
        # Otherwise only show approved or answered questions
        return queryset.filter(
            status__in=[Question.Status.APPROVED, Question.Status.ANSWERED]
        ).sorted_by_votes()
    
    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        """Enhance the template context with additional data."""
        context = super().get_context_data(**kwargs)
        context["talk"] = self.talk
        context["user_can_moderate"] = self.request.user.is_staff or self.request.user.is_superuser
        
        # Add form for new questions
        context["question_form"] = QuestionCreateView.form_class()
        
        # For each question, check if the current user has voted
        if self.request.user.is_authenticated:
            user_voted_questions = set(
                QuestionVote.objects.filter(
                    user=self.request.user, 
                    question__talk=self.talk
                ).values_list("question_id", flat=True)
            )
            
            for question in context["questions"]:
                question.user_voted = question.id in user_voted_questions
                
        return context


class QuestionCreateView(LoginRequiredMixin, CreateView):
    """
    Create a new question for a talk.
    
    Requires login to create questions.
    """

    model = Question
    template_name = "talks/questions/question_form.html"
    fields = ["content", "is_anonymous"]
    
    def form_valid(self, form):
        """Process the form submission."""
        # Set the talk and user
        form.instance.talk = get_object_or_404(Talk, pk=self.kwargs["talk_id"])
        form.instance.user = self.request.user
        
        # Set author name/email from user if available
        if not form.instance.is_anonymous:
            form.instance.author_name = getattr(self.request.user, "get_full_name", lambda: "")() or self.request.user.email
            form.instance.author_email = self.request.user.email
            
        # Save the question
        response = super().form_valid(form)
        
        # Show success message
        messages.success(self.request, _("Your question has been submitted and is awaiting moderation."))
        
        # If this is an HTMX request, return to the question list
        if self.request.headers.get("HX-Request"):
            return render(
                self.request,
                "talks/questions/question_success.html",
                {"question": form.instance}
            )
            
        return response
    
    def get_success_url(self):
        """Redirect to the talk's Q&A page."""
        return reverse("talk_questions", args=[self.kwargs["talk_id"]])


@require_POST
@login_required
def vote_question(request: HttpRequest, question_id: int) -> HttpResponse:
    """
    Handle voting for a question.
    
    If the user has already voted, the vote is removed (toggle behavior).
    Returns JSON with the updated vote count.
    """
    question = get_object_or_404(Question, pk=question_id)
    
    # Check if user has already voted
    existing_vote = QuestionVote.objects.filter(
        question=question,
        user=request.user
    ).first()
    
    if existing_vote:
        # Remove vote if it exists
        existing_vote.delete()
        action = "removed"
    else:
        # Create new vote
        QuestionVote.objects.create(
            question=question,
            user=request.user
        )
        action = "added"
    
    # Return updated vote count
    return JsonResponse({
        "vote_count": question.votes.count(),
        "action": action
    })


# Moderator views
def is_moderator(user) -> bool:
    """Check if the user is a moderator (staff or superuser)."""
    return user.is_staff or user.is_superuser


class ModeratorRequiredMixin(UserPassesTestMixin):
    """Mixin to require moderator permissions."""
    
    def test_func(self):
        """Check if the user is a moderator."""
        return is_moderator(self.request.user)


@require_POST
@user_passes_test(is_moderator)
def approve_question(request: HttpRequest, question_id: int) -> HttpResponse:
    """Approve a question."""
    question = get_object_or_404(Question, pk=question_id)
    question.approve()
    messages.success(request, _("Question has been approved."))
    return redirect("talk_questions", talk_id=question.talk.id)


@require_POST
@user_passes_test(is_moderator)
def reject_question(request: HttpRequest, question_id: int) -> HttpResponse:
    """Reject a question."""
    question = get_object_or_404(Question, pk=question_id)
    question.reject()
    messages.success(request, _("Question has been rejected."))
    return redirect("talk_questions", talk_id=question.talk.id)


@require_POST
@user_passes_test(is_moderator)
def mark_question_answered(request: HttpRequest, question_id: int) -> HttpResponse:
    """Mark a question as answered."""
    question = get_object_or_404(Question, pk=question_id)
    question.mark_as_answered()
    messages.success(request, _("Question has been marked as answered."))
    return redirect("talk_questions", talk_id=question.talk.id)


class AnswerCreateView(ModeratorRequiredMixin, CreateView):
    """
    Create an answer to a question.
    
    Only moderators can create official answers.
    """

    model = Answer
    template_name = "talks/questions/answer_form.html"
    fields = ["content", "is_official"]
    
    def form_valid(self, form):
        """Process the form submission."""
        # Set the question and user
        question_id = self.kwargs["question_id"]
        form.instance.question = get_object_or_404(Question, pk=question_id)
        form.instance.user = self.request.user
        
        # Save the answer
        response = super().form_valid(form)
        
        # Show success message
        messages.success(self.request, _("Answer has been posted."))
        return response
    
    def get_success_url(self):
        """Redirect to the talk's Q&A page."""
        question = get_object_or_404(Question, pk=self.kwargs["question_id"])
        return reverse("talk_questions", args=[question.talk.id])