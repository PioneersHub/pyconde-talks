"""URL configuration for the talks app."""

from django.urls import path

from .views import (
    TalkDetailView,
    TalkListView,
    dashboard_stats,
    talk_redirect_view,
    upcoming_talks,
)
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


urlpatterns = [
    path("", TalkListView.as_view(), name="talk_list"),
    path("<int:pk>/", TalkDetailView.as_view(), name="talk_detail"),
    path("dashboard-stats/", dashboard_stats, name="dashboard_stats"),
    path("upcoming-talks/", upcoming_talks, name="upcoming_talks"),
    path("<str:talk_id>/", talk_redirect_view, name="talk_redirect"),
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
