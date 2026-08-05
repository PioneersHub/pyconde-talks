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
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.core.exceptions import PermissionDenied
from django.db.models import Q
from django.http import Http404, HttpRequest, HttpResponse, HttpResponseBase, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.translation import (
    gettext_lazy as _,
    ngettext,
)
from django.views.decorators.http import require_POST, require_safe
from django.views.generic import CreateView, ListView, UpdateView

from .forms_qa import QuestionForm
from .models import Talk, is_qa_moderator, user_can_join_qa
from .models_qa import Question, QuestionQuerySet, QuestionVote
from .ratelimit import RateLimit, claim, question_limits, refund, seconds_until_reset
from .spam import spam_flag_reason
from .utils import get_talk_by_id_or_pretalx, is_htmx_request


if TYPE_CHECKING:
    from django.contrib.auth.models import AbstractBaseUser, AnonymousUser
    from django.utils.functional import _StrPromise as StrOrPromise

    from users.models import CustomUser


# Scope name for the question-asking allowance, shared by the claim and the refund.
_QA_QUESTION_SCOPE = "qa_question"

# Methods that only read. They never claim an allowance and never store anything.
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

# The status filters the Q&A views understand. Anything else collapses to "all" so an attacker
# cannot reflect arbitrary text into the hx-vals JSON / hx-get URL the list fragment builds.
_VALID_STATUS_FILTERS = frozenset(
    {"all", "mine", "pending", "approved", "answered", "rejected"},
)


def _get_status_filter(request: HttpRequest) -> str:
    """Return a validated status_filter from POST (hx-vals) or GET, defaulting to 'all'."""
    raw = request.POST.get("status_filter") or request.GET.get("status_filter", "all")
    return raw if raw in _VALID_STATUS_FILTERS else "all"


# Marks a response as a Q&A error that HTMX should swap despite its 4xx status. The listener in
# ``base.html`` keys on this header rather than on the status code alone: keyed on the code, every
# HTMX control on the site would swap Django's 404 page, the bare 403 page or the CSRF failure
# page into whatever small div happened to fire the request.
QA_ERROR_HEADER = "HX-Qa-Error"

# Where a Q&A error is delivered, whatever element triggered the request. The moderation and vote
# buttons target ``#question-list`` with ``outerHTML``, and they inherit ``hx-select`` from the
# fragment root, so without redirecting all three of those the error body would be filtered to
# nothing and the swap would delete the whole thread along with its ten-second poller.
_QA_ERROR_TARGET = "#question-error"
_QA_ERROR_SELECT = "#qa-error-body"


def _qa_error_response(
    request: HttpRequest,
    message: str | StrOrPromise,
    status: HTTPStatus,
    talk_id: int,
) -> HttpResponse:
    """
    Return a Q&A error as an HTMX fragment, or flash it and redirect for a plain request.

    The Q&A form posts into a small target div, so the error has to arrive as markup that can be
    swapped in. HTMX does not swap 4xx bodies by default; ``base.html`` opts into that for responses
    carrying ``QA_ERROR_HEADER``, so an honest status code can be used here instead of a misleading
    200.

    The retarget/reswap/reselect headers put the message in the page's dedicated error region
    regardless of which control was clicked, rather than in whatever that control happened to
    target.
    """
    if is_htmx_request(request):
        response = render(
            request,
            "talks/questions/question_error.html",
            {"message": message},
            status=status,
        )
        response[QA_ERROR_HEADER] = "1"
        response["HX-Retarget"] = _QA_ERROR_TARGET
        response["HX-Reswap"] = "innerHTML"
        response["HX-Reselect"] = _QA_ERROR_SELECT
        return response
    messages.error(request, message)
    return redirect("talk_questions", talk_id=talk_id)


def _question_is_visible_to(question: Question, user: AbstractBaseUser | AnonymousUser) -> bool:
    """
    Return whether *user* is shown *question* in the Q&A thread.

    The same rule ``_regular_user_questions`` applies to the list: the published thread is for
    everyone, while a held or rejected question belongs to its author and to moderators.
    """
    if question.status in _PUBLIC_STATUSES:
        return True
    return question.user_id == user.pk or is_moderator(user)


def _get_accessible_question(
    user: AbstractBaseUser | AnonymousUser,
    question_id: int,
    *,
    visible_only: bool = False,
) -> Question:
    """
    Return the question if the user has access to its talk's event, or raise Http404.

    A talk whose event has the Q&A disabled yields a 404 for every caller, not just the list view:
    switching an event off has to close the write endpoints too, or votes and moderation would keep
    landing on a Q&A that no longer exists as far as the site is concerned.

    *visible_only* additionally withholds questions the requester is not entitled to see. Only
    voting needs it, and it needs it badly: without it a 200 confirms that a question exists at an
    id whose content the requester is never shown, and the vote itself reorders the vote-sorted
    moderator queue.

    ``select_related("talk__event")`` because every caller now reads the event's Q&A mode.
    """
    question = get_object_or_404(
        Question.objects.select_related("talk", "talk__event"),
        pk=question_id,
    )
    accessible = Talk.objects.accessible_to(user)
    if not accessible.filter(pk=question.talk_id).exists():
        raise Http404
    if not question.talk.event.qa_visible:
        raise Http404
    if visible_only and not _question_is_visible_to(question, user):
        raise Http404
    return question


class QuestionListView(LoginRequiredMixin, ListView[Question]):
    """
    Display a list of questions for a specific talk.

    Questions are sorted by vote count, with the most popular at the top. Only approved, answered
    and their own questions are shown to regular users. Moderators can see all questions including
    pending ones.
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
        # Reading the thread is open to anyone who can see the talk; taking part is not. Without
        # this the form would be offered to a visitor whose submission is then refused.
        context["user_can_join_qa"] = user_can_join_qa(self.request.user, self.talk.event)
        context["status_filter"] = self.status_filter
        # Only this page needs the key, so it goes in the view rather than a context processor
        # that would run on every request including the ten-second poll.
        context["turnstile_site_key"] = settings.TURNSTILE_SITE_KEY
        return context


class QuestionCreateView(LoginRequiredMixin, CreateView[Question, forms.ModelForm[Question]]):
    """
    Create a new question for a talk.

    Requires login to create questions, and an event whose Q&A is still accepting them.
    """

    model = Question
    form_class = QuestionForm

    def dispatch(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponseBase:
        """
        Turn the submission away when the visitor or the event's Q&A mode does not allow it.

        Checked before the form is even bound, so a closed Q&A costs nothing to reject and cannot be
        talked into storing a question by a well-formed POST.

        A safe method never reaches those checks. There is no standalone create template - the form
        is embedded in the question list, and ``template_name`` used to name a file that does not
        exist, so a GET here was a 500. It redirects to the page that has the form, and it does so
        before the rate limit is claimed, or simply opening the URL would spend a question from the
        author's allowance.
        """
        if request.method in SAFE_METHODS:
            return redirect("talk_questions", talk_id=self.kwargs["talk_id"])

        if request.user.is_authenticated:
            self.talk = get_object_or_404(
                Talk.objects.accessible_to(request.user).select_related("event"),
                pk=self.kwargs["talk_id"],
            )
            if not self.talk.event.qa_visible:
                raise Http404
            refusal = self._refusal(request)
            if refusal is not None:
                return refusal
        return super().dispatch(request, *args, **kwargs)

    def _refusal(self, request: HttpRequest) -> HttpResponse | None:
        """
        Return the reason this submission cannot be accepted, or None to let it through.

        Order matters: the cheap, permanent reasons come before the rate limit, so a visitor who may
        not post here at all is told that rather than being counted against an allowance they were
        never going to use. ``GET`` never reaches this - it redirects above - so nothing here can be
        triggered by merely opening the page.
        """
        if not self.talk.event.qa_accepts_questions:
            return _qa_error_response(
                request,
                _("Questions are closed for this talk."),
                HTTPStatus.CONFLICT,
                self.talk.pk,
            )
        if not user_can_join_qa(request.user, self.talk.event):
            return _qa_error_response(
                request,
                _("Only ticket holders can ask questions about this event."),
                HTTPStatus.FORBIDDEN,
                self.talk.pk,
            )
        return self._rate_limit_response(request)

    def _rate_limit_response(self, request: HttpRequest) -> HttpResponse | None:
        """
        Claim one question against this account's allowance, or return an error response.

        Claiming rather than peeking, so a burst of concurrent POSTs cannot all pass a check that
        none of them had counted yet. The claim is refunded in ``form_invalid`` when the submission
        turns out not to be storable, so a rejected draft costs nothing.

        Moderators are exempt: they are the people expected to post repeatedly, and they are the
        ones who would have to unpick a limit that caught them.
        """
        if is_moderator(request.user):
            return None

        for scope, identity, rule in self._allowances():
            if not claim(scope, identity, rule):
                minutes = max(round(seconds_until_reset(rule) / 60), 1)
                return _qa_error_response(
                    request,
                    ngettext(
                        "You have asked several questions recently. Please wait about "
                        "%(minutes)d minute before asking another.",
                        "You have asked several questions recently. Please wait about "
                        "%(minutes)d minutes before asking another.",
                        minutes,
                    )
                    % {"minutes": minutes},
                    HTTPStatus.TOO_MANY_REQUESTS,
                    self.talk.pk,
                )
        return None

    def _allowances(self) -> tuple[tuple[str, str, RateLimit], ...]:
        """
        Return the (scope, identity, rule) triples this account is measured against.

        One shared definition, because the claim and the refund have to key on exactly the same
        buckets. Getting that wrong would leak allowance in one direction or the other.
        """
        per_talk, overall = question_limits()
        user_pk = self.request.user.pk
        return (
            (_QA_QUESTION_SCOPE, f"{user_pk}:{self.talk.pk}", per_talk),
            (_QA_QUESTION_SCOPE, str(user_pk), overall),
        )

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

        # Save the question. The allowance was already claimed in ``dispatch``, atomically, and
        # is refunded in ``form_invalid`` if we never get here.
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
        template to re-render. Return a 422 with the error for HTMX (mirroring the rating views), or
        flash it and redirect back otherwise.

        Collects errors from every field rather than only ``content``: the captcha check adds its
        own, and those would otherwise fall through to the generic fallback message.

        Refunds the allowance claimed in ``dispatch``. The claim has to happen before the content is
        validated for the limit to be atomic, but nothing was posted, so nothing should be charged
        for.
        """
        if not is_moderator(self.request.user):
            for scope, identity, rule in self._allowances():
                refund(scope, identity, rule)

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

    A pending question is visible to its author and to moderators, nobody else: the author needs to
    see that their question was received rather than silently swallowed, while for everyone else the
    queue is the whole point of pre-moderation.

    A disabled Q&A yields nothing to anyone, moderators included, so switching an event off cannot
    keep serving content through a stale tab's ten-second poll.
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

    Asking for "pending" or "rejected" by hand is allowed but not privileged: it narrows to their
    own, never to everyone's.
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

    If the user has already voted, the vote is removed (toggle behavior). Returns HTML for HTMX to
    replace the voting div.

    ``visible_only``: you may only vote on a question you can actually read. Otherwise a bystander
    could walk the id space, and a 200 would tell them a held question exists there while the JSON
    body handed back its vote count.

    Voting also needs a relationship with the event, like asking does. A vote is what orders the
    thread and the moderator queue, so an outsider with no ticket for the event running now should
    not be steering either.
    """
    question = _get_accessible_question(request.user, question_id, visible_only=True)
    if not user_can_join_qa(request.user, question.talk.event):
        return _qa_error_response(
            request,
            _("Only ticket holders can vote on questions about this event."),
            HTTPStatus.FORBIDDEN,
            question.talk_id,
        )

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

    ``LoginRequiredMixin`` comes first so an anonymous visitor is redirected to log in rather than
    getting the 403 that ``UserPassesTestMixin`` raises when its test fails.
    """

    model = Question
    fields = ("content",)
    template_name = "talks/questions/question_edit_form.html"
    pk_url_kwarg = "question_id"

    def dispatch(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponseBase:
        """
        Turn the edit away once the event's Q&A stops accepting questions.

        An edit replaces the body wholesale, so leaving this open would make editing the way to post
        new content after a freeze - the one thing freezing is for. A disabled Q&A 404s through
        ``_get_accessible_question``, like every other entry point.

        Checked before the form is bound, and only for the author: ``test_func`` is
        ``QuestionOwnerRequiredMixin``'s, and deferring to it for everyone else preserves a non-
        owner's 403, which the access lookup here would otherwise turn into a 404.
        """
        if request.user.is_authenticated and self.test_func():
            question = _get_accessible_question(request.user, self.kwargs["question_id"])
            if not question.talk.event.qa_accepts_questions:
                return _qa_error_response(
                    request,
                    _("Questions are closed for this talk."),
                    HTTPStatus.CONFLICT,
                    question.talk_id,
                )
        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self) -> QuestionQuerySet:
        """
        Scope editable questions to talks the user can still access.

        ``QuestionOwnerRequiredMixin`` only checks ownership; without this, a user who lost access
        to a talk's event (ticket revoked, event deactivated) could still GET/POST the edit form for
        their old question and the HTMX response would leak that talk's question list. Mirrors the
        ``accessible_to`` scoping used by every other endpoint in this module.
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
        """Persist changes, reset the votes, and re-decide the status as if newly asked."""
        question: Question = form.instance
        was_published = question.status in _PUBLIC_STATUSES

        # An edit replaces the body, so the old approval no longer applies to what is there now.
        # Re-decide exactly as ``QuestionCreateView`` would for new content: held on a moderated
        # event, held if it looks like spam, published otherwise. Without this, the way past both
        # is the same one: post something innocuous, wait for it to publish, then edit.
        reason = spam_flag_reason(question.content)
        holds = question.talk.event.qa_holds_for_review or bool(reason)
        sent_back = holds and was_published
        if sent_back:
            question.status = Question.Status.PENDING
            question.flag_reason = reason

        response = super().form_valid(form)

        # Reset the count. Votes were cast on the previous wording, so they say nothing about
        # this one. The author's own vote stays, which leaves the question exactly where a newly
        # asked one starts, rather than below it.
        QuestionVote.objects.filter(question=self.object).exclude(user=self.request.user).delete()

        if sent_back and reason:
            messages.warning(
                self.request,
                _(
                    "Your question was updated. It needs another look from a moderator before it "
                    "reappears, and its votes were reset.",
                ),
            )
        elif sent_back:
            messages.warning(
                self.request,
                _(
                    "Your question was updated and is waiting for a moderator to approve it "
                    "again. Its votes were reset.",
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
    """
    Check if the user is a moderator (staff or superuser).

    Delegates to ``talks.models.is_qa_moderator``, which is the same rule spelled where the access
    predicates can reach it without importing the view layer.
    """
    return is_qa_moderator(user)


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
