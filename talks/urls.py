"""
URL configuration for the talks app.

``LoginRequiredMiddleware`` gates everything by default, so a URL is public only if it is
wrapped in ``login_not_required`` below. That wrapper opens a view completely, for every
method, so only the read-only browsing views get it: what an anonymous visitor actually sees
is then decided per row by ``TalkQuerySet.accessible_to`` and, for recordings, by the video
gate. Q&A, ratings, bookmarks and the chair tooling stay closed, and also carry their own
``LoginRequiredMixin`` / ``@login_required`` so this file is not the only thing holding them
shut. ``talks/tests/test_access_policy.py`` pins that split.
"""

from django.contrib.auth.decorators import login_not_required
from django.urls import path

from .views import (
    TalkDetailView,
    TalkListView,
    dashboard_stats,
    talk_redirect_view,
    upcoming_talks,
)
from .views_chair import chair_grid_view, toggle_session_chair
from .views_qa import (
    QuestionCreateView,
    QuestionListView,
    QuestionUpdateView,
    approve_question,
    delete_question,
    mark_question_answered,
    question_redirect_view,
    reject_question,
    vote_question,
)
from .views_rating import delete_rating, get_talk_rating_stats, rate_talk
from .views_saved import merge_saved_talks, toggle_save_talk
from .views_schedule import schedule_view


urlpatterns = [
    path("", login_not_required(TalkListView.as_view()), name="talk_list"),
    path("schedule/", login_not_required(schedule_view), name="schedule"),
    path("chairs/", chair_grid_view, name="chair_grid"),
    path("<int:pk>/", login_not_required(TalkDetailView.as_view()), name="talk_detail"),
    path("dashboard-stats/", login_not_required(dashboard_stats), name="dashboard_stats"),
    path("upcoming-talks/", login_not_required(upcoming_talks), name="upcoming_talks"),
    # Rating URLs
    path("<int:talk_id>/rate/", rate_talk, name="rate_talk"),
    path("<int:talk_id>/rate/delete/", delete_rating, name="delete_rating"),
    path("<int:talk_id>/rating-stats/", get_talk_rating_stats, name="talk_rating_stats"),
    # Save/Bookmark URLs
    path("<int:talk_id>/save/", toggle_save_talk, name="toggle_save_talk"),
    path("saved/merge/", merge_saved_talks, name="merge_saved_talks"),
    # Session-chair URLs
    path("<int:talk_id>/chair/", toggle_session_chair, name="toggle_session_chair"),
    path("<str:talk_id>/", login_not_required(talk_redirect_view), name="talk_redirect"),
    # Q&A URLs
    path("<int:talk_id>/questions/", QuestionListView.as_view(), name="talk_questions"),
    path("<str:talk_id>/questions/", question_redirect_view, name="question_redirect"),
    path(
        "<int:talk_id>/questions/new/",
        QuestionCreateView.as_view(),
        name="question_create",
    ),
    path("questions/<int:question_id>/vote/", vote_question, name="question_vote"),
    path("questions/<int:question_id>/edit/", QuestionUpdateView.as_view(), name="question_edit"),
    path("questions/<int:question_id>/delete/", delete_question, name="question_delete"),
    path("questions/<int:question_id>/reject/", reject_question, name="question_reject"),
    path(
        "questions/<int:question_id>/mark-answered/",
        mark_question_answered,
        name="question_mark_answered",
    ),
    path("questions/<int:question_id>/approve/", approve_question, name="question_approve"),
]
