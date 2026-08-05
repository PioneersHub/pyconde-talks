"""
Views for managing and displaying Question and Answer objects.

This module provides class-based and function-based views for handling Question and Answer including
listing, creating, voting, and moderation actions.

Every view here requires a login, at every event visibility including public. Moderating Q&A is
volunteer work, so opening an event's recordings does not also open its Q&A. The requirement is
declared per view rather than left to ``LoginRequiredMiddleware``: browsing URLs are becoming
public, and a stray ``login_not_required`` on a URL would otherwise quietly expose these too.
"""

from http import HTTPStatus
from typing import TYPE_CHECKING, Any, cast

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.core.exceptions import PermissionDenied
from django.db.models import Q
from django.http import Http404, HttpRequest, HttpResponse, HttpResponseBase, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_POST, require_safe
from django.views.generic import CreateView, ListView, UpdateView

from .models import Talk
from .models_qa import Question, QuestionQuerySet, QuestionVote
from .spam import spam_flag_reason
from .utils import get_talk_by_id_or_pretalx, is_htmx_request


if TYPE_CHECKING:
    from django.contrib.auth.models import AbstractBaseUser, AnonymousUser
    from django.utils.functional import _StrPromise as StrOrPromise

    from users.models import CustomUser


# The status filters the Q&A views understand. Anything else collapses to "all" so an attacker
# cannot reflect arbitrary text into the hx-vals JSON / hx-get URL the list fragment builds.
_VALID_STATUS_FILTERS = frozenset(
    {"all", "mine", "pending", "approved", "answered", "rejected"},
)


def _get_status_filter(request: HttpRequest) -> str:
    """Return a validated status_filter from POST (hx-vals) or GET, defaulting to 'all'."""
    raw = request.POST.get("status_filter") or request.GET.get("status_filter", "all")
    return raw if raw in _VALID_STATUS_FILTERS else "all"


def _qa_error_response(
    request: HttpRequest,
    message: str | StrOrPromise,
    status: HTTPStatus,
    talk_id: int,
) -> HttpResponse:
    """
    Return a Q&A error as an HTMX fragment, or flash it and redirect for a plain request.

    The Q&A form posts into a small target div, so the error has to arrive as markup that can
    be swapped in. HTMX does not swap 4xx bodies by default; ``base.html`` opts into that, so
    an honest status code can be used here instead of a misleading 200.
    """
    if is_htmx_request(request):
        return render(
            request,
            "talks/questions/question_error.html",
            {"message": message},
            status=status,
        )
    messages.error(request, message)
    return redirect("talk_questions", talk_id=talk_id)


def _get_accessible_question(user: AbstractBaseUser | AnonymousUser, question_id: int) -> Question:
    """Return the question if the user has access to its talk's event, or raise Http404."""
    question = get_object_or_404(Question.objects.select_related("talk"), pk=question_id)
    accessible = Talk.objects.accessible_to(cast("CustomUser", user))
    if not accessible.filter(pk=question.talk_id).exists():
        raise Http404
    return question


class QuestionListView(LoginRequiredMixin, ListView[Question]):
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
        # select_related("event"): every Q&A mode check reads it, which would otherwise be an
        # extra query per check.
        self.talk = get_object_or_404(
            Talk.objects.accessible_to(user).select_related("event"),
            pk=self.kwargs["talk_id"],
        )
        if not self.talk.event.qa_visible:
            # A disabled Q&A should look absent, not merely closed.
            raise Http404

        # Get the (validated) status filter from the request
        self.status_filter = _get_status_filter(self.request)

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


class QuestionCreateView(LoginRequiredMixin, CreateView[Question, forms.ModelForm[Question]]):
    """
    Create a new question for a talk.

    Requires login to create questions, and an event whose Q&A is still accepting them.
    """

    model = Question
    template_name = "talks/questions/question_form.html"
    fields = ("content",)

    def dispatch(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponseBase:
        """
        Turn the submission away when the event's Q&A is frozen or disabled.

        Checked before the form is even bound, so a closed Q&A costs nothing to reject and
        cannot be talked into storing a question by a well-formed POST.
        """
        if request.user.is_authenticated:
            self.talk = get_object_or_404(
                Talk.objects.accessible_to(request.user).select_related("event"),
                pk=self.kwargs["talk_id"],
            )
            if not self.talk.event.qa_visible:
                raise Http404
            if not self.talk.event.qa_accepts_questions:
                return _qa_error_response(
                    request,
                    _("Questions are closed for this talk."),
                    HTTPStatus.CONFLICT,
                    self.kwargs["talk_id"],
                )
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form: forms.ModelForm[Question]) -> HttpResponse:
        """Process the form submission."""
        question: Question = form.instance

        # ``dispatch`` already resolved and access-checked the talk.
        question.talk = self.talk
        question.user = self.request.user

        # Hold the question when the event pre-moderates, or when it looks like spam even on an
        # otherwise open Q&A. The reason is recorded either way, so a moderator working a
        # moderated event can still see which items were auto-flagged and triage those first.
        reason = spam_flag_reason(question.content)
        if self.talk.event.qa_holds_for_review or reason:
            question.status = Question.Status.PENDING
            question.flag_reason = reason

        # Save the question
        response = super().form_valid(form)

        # Auto vote your own question. Worth doing even while it is held for review, so the
        # count is right the moment a moderator approves it.
        QuestionVote.objects.get_or_create(
            question=question,
            user=self.request.user,
        )

        # Show success message
        if question.status == Question.Status.PENDING:
            messages.success(
                self.request,
                _("Your question was submitted and is waiting for a moderator to approve it."),
            )
        else:
            messages.success(self.request, _("Your question has been posted."))

        # If this is an HTMX request, return to the question list
        if is_htmx_request(self.request):
            user_can_moderate = is_moderator(self.request.user)
            status_filter = _get_status_filter(self.request)
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

    def form_invalid(self, form: forms.ModelForm[Question]) -> HttpResponse:
        """
        Reject an invalid submission (e.g. content over CONTENT_MAX_LENGTH) without a 500.

        The create form is embedded in the question list page, so there is no standalone form
        template to re-render. Return a 422 with the error for HTMX (mirroring the rating views),
        or flash it and redirect back otherwise.

        Collects errors from every field rather than only ``content``: the captcha check adds
        its own, and those would otherwise fall through to the generic fallback message.
        """
        errors = [str(error) for field_errors in form.errors.values() for error in field_errors]
        message = "; ".join(errors) if errors else str(_("Your question could not be posted."))
        return _qa_error_response(
            self.request,
            message,
            HTTPStatus.UNPROCESSABLE_ENTITY,
            self.kwargs["talk_id"],
        )

    def get_success_url(self) -> str:
        """Redirect to the talk's Q&A page."""
        return reverse("talk_questions", args=[self.kwargs["talk_id"]])


_STATUS_Q: dict[str, Q] = {
    "pending": Q(status=Question.Status.PENDING),
    "approved": Q(status=Question.Status.APPROVED),
    "answered": Q(status=Question.Status.ANSWERED),
    "rejected": Q(status=Question.Status.REJECTED),
}

# Statuses everyone may see, whoever asked the question.
_PUBLIC_STATUSES = (Question.Status.APPROVED, Question.Status.ANSWERED)
# Statuses only the author (and moderators) may see: held back or turned down.
_AUTHOR_ONLY_STATUSES = (Question.Status.PENDING, Question.Status.REJECTED)
# The matching filter values, for the two paths that narrow to one of those statuses.
_AUTHOR_ONLY_FILTERS = ("pending", "rejected")


def get_filtered_questions(
    request: HttpRequest,
    talk: Talk,
    status_filter: str = "all",
) -> QuestionQuerySet:
    """
    Get filtered questions based on user permissions and filter selection.

    This function centralizes the filtering logic used in both QuestionListView and vote_question.

    A pending question is visible to its author and to moderators, nobody else: the author needs
    to see that their question was received rather than silently swallowed, while for everyone
    else the queue is the whole point of pre-moderation.

    A disabled Q&A yields nothing to anyone, moderators included, so switching an event off
    cannot keep serving content through a stale tab's ten-second poll.
    """
    if not talk.event.qa_visible:
        return Question.objects.none()

    queryset = Question.objects.filter(talk=talk).select_related("user")

    if status_filter == "mine":
        return queryset.filter(user=request.user).sorted_by_votes()

    # "approved" and "answered" work the same for everyone
    if status_filter in ("approved", "answered"):
        return queryset.filter(_STATUS_Q[status_filter]).sorted_by_votes()

    if is_moderator(request.user):
        return _moderator_questions(queryset, status_filter)
    return _regular_user_questions(queryset, request.user, status_filter)


def _moderator_questions(
    queryset: QuestionQuerySet,
    status_filter: str,
) -> QuestionQuerySet:
    """Return the queue a moderator sees: everything, or one status in full."""
    if status_filter in _AUTHOR_ONLY_FILTERS:
        return queryset.filter(_STATUS_Q[status_filter]).sorted_by_votes()
    return queryset.sorted_by_votes()


def _regular_user_questions(
    queryset: QuestionQuerySet,
    user: AbstractBaseUser | AnonymousUser,
    status_filter: str,
) -> QuestionQuerySet:
    """
    Return what an ordinary attendee sees: the public thread plus their own held questions.

    Asking for "pending" or "rejected" by hand is allowed but not privileged: it narrows to
    their own, never to everyone's.
    """
    if status_filter in _AUTHOR_ONLY_FILTERS:
        return queryset.filter(_STATUS_Q[status_filter], user=user).sorted_by_votes()
    return queryset.filter(
        Q(status__in=_PUBLIC_STATUSES) | Q(status__in=_AUTHOR_ONLY_STATUSES, user=user),
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


@login_required
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


@login_required
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
    LoginRequiredMixin,
    QuestionOwnerRequiredMixin,
    UpdateView[Question, forms.ModelForm[Question]],
):
    """
    Allow a question owner to edit content; clears votes upon successful update.

    ``LoginRequiredMixin`` comes first so an anonymous visitor is redirected to log in rather
    than getting the 403 that ``UserPassesTestMixin`` raises when its test fails.
    """

    model = Question
    fields = ("content",)
    template_name = "talks/questions/question_edit_form.html"
    pk_url_kwarg = "question_id"

    def get_queryset(self) -> QuestionQuerySet:
        """
        Scope editable questions to talks the user can still access.

        ``QuestionOwnerRequiredMixin`` only checks ownership; without this, a user who lost
        access to a talk's event (ticket revoked, event deactivated) could still GET/POST the
        edit form for their old question and the HTMX response would leak that talk's question
        list. Mirrors the ``accessible_to`` scoping used by every other endpoint in this module.
        """
        user = cast("CustomUser", self.request.user)
        return Question.objects.filter(talk__in=Talk.objects.accessible_to(user))

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
        # Re-run the spam heuristics. Otherwise the way past them is obvious: post something
        # innocuous, wait for it to publish, then edit the links in.
        reason = spam_flag_reason(form.instance.content)
        sent_back = reason and form.instance.status == Question.Status.APPROVED
        if sent_back:
            form.instance.status = Question.Status.PENDING
            form.instance.flag_reason = reason

        # Save updated content
        response = super().form_valid(form)
        # Clear all votes (except your own) after content change
        QuestionVote.objects.filter(question=self.object).exclude(user=self.request.user).delete()
        if sent_back:
            messages.warning(
                self.request,
                _(
                    "Your question was updated and sent back for review. Previous votes were "
                    "cleared.",
                ),
            )
        else:
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


@login_required
@require_POST
def reject_question(request: HttpRequest, question_id: int) -> HttpResponse:
    """Reject a question."""
    return _moderate_question(
        request,
        question_id,
        "reject",
        _("Question has been rejected."),
    )


@login_required
@require_POST
def mark_question_answered(request: HttpRequest, question_id: int) -> HttpResponse:
    """Mark a question as answered."""
    return _moderate_question(
        request,
        question_id,
        "mark_as_answered",
        _("Question has been marked as answered."),
    )


@login_required
@require_POST
def approve_question(request: HttpRequest, question_id: int) -> HttpResponse:
    """Approve a question."""
    return _moderate_question(
        request,
        question_id,
        "approve",
        _("Question has been approved."),
    )


@login_required
@require_safe
def question_redirect_view(request: HttpRequest, talk_id: str) -> HttpResponse:
    """Get talk question view by Talk ID or pretalx_id."""
    talk = get_talk_by_id_or_pretalx(talk_id, user=cast("CustomUser", request.user))
    if talk:
        return redirect("talk_questions", talk_id=talk.pk)
    msg = f"No talk found with ID or pretalx ID: {talk_id}"
    raise Http404(msg)
