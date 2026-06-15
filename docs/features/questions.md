---
icon: lucide/message-circle
---

# Questions and answers

Each talk has a live Q&A page where attendees can ask questions, vote questions up, and watch the
list re-order in real time. Moderators approve, reject, and mark questions as answered. The page is
built to survive a busy room: voting is a safe toggle, the list auto-refreshes, and content is
bounded so a single attendee cannot flood it.

Source:
[`talks/views_qa.py`](https://github.com/PioneersHub/pyconde-talks/blob/main/talks/views_qa.py),
models
[`talks/models_qa.py`](https://github.com/PioneersHub/pyconde-talks/blob/main/talks/models_qa.py),
admin
[`talks/admin_qa.py`](https://github.com/PioneersHub/pyconde-talks/blob/main/talks/admin_qa.py). The
page is at `/<talk-id>/questions/` and is also reachable by Pretalx code via a redirect view.

## The data model

Three models back the feature:

- **`Question`**: belongs to a talk, carries the question text, an optional author (nullable so a
    deleted user does not delete the question), a status, and timestamps. The content is capped at
    `CONTENT_MAX_LENGTH` (2000 characters).
- **`QuestionVote`**: one row per `(question, user)`, enforced by a unique constraint so a user
    cannot double-vote.
- **`Answer`**: belongs to a question, carries the answer text (same 2000-character cap), an
    optional author, and an `is_official` flag for answers from a speaker or organizer. Saving an
    answer moves the question to "answered" unless it has been rejected.

A question has three states (`Question.Status`): **approved**, **answered**, and **rejected**. New
questions default to **approved**, so they are visible immediately; moderators use rejected to hide
abuse and answered to mark resolved questions.

## Asking a question

The ask form is embedded at the top of the Q&A page. On submit (`QuestionCreateView`):

1. The talk is resolved through `accessible_to`, so a user cannot post to a talk outside their event
    scope.
2. The question is saved and the author auto-votes their own question, so it starts with one vote.
3. A success message is shown and, for HTMX, the question list is returned so the new question
    appears without a reload.

The content cap is enforced by the auto-generated form. An over-length submission does not 500: the
HTMX path returns a 422 with the validation error, and the non-HTMX path flashes the error and
redirects back.

A logged-in user can edit their own question (`QuestionUpdateView`). Editing clears all votes except
the author's own, because the votes were cast on the old wording, and the attendee is told their
votes were reset. A user can delete their own question; moderators can delete any question.

## Voting

Voting is a toggle. Posting to `vote_question` creates a vote if none exists, or removes it if it
does. The create uses `get_or_create` against the unique constraint, so two near-simultaneous clicks
cannot both insert a vote and crash with an integrity error.

After a vote, the whole question list is re-rendered sorted by vote count (descending), then by
recency, so the most-wanted questions float to the top. Non-HTMX clients get a small JSON response
with the new count and the user's vote state.

## Filtering and what each user sees

The list can be filtered by status via the `status_filter` param. The allowed values are validated
against an allowlist (`all`, `mine`, `approved`, `answered`, `rejected`); anything else collapses to
`all`. This is a deliberate guard: the value is reflected into the `hx-vals` and `hx-get` of the
list fragment, so unvalidated input could otherwise be injected there.

What each role sees, from `get_filtered_questions`:

- **Regular attendees**: approved and answered questions, plus their own rejected questions. The
    `mine` filter shows only their own; `approved` and `answered` work the same for everyone.
- **Moderators**: everything, including all rejected questions, and may select the `rejected`
    filter.

The filter dropdown surfaces All, My questions, Active (approved), and Answered to everyone, and
adds Rejected for moderators.

## Moderation

A moderator is any authenticated user who is staff or a superuser (`is_moderator`). Moderators see a
banner explaining their elevated view and get inline action buttons on each question:

- **Approved** questions can be **rejected** (hidden from the public) or **marked answered**.
- **Answered** questions can be **rejected**.
- **Rejected** questions can be **approved** again, to recover from a misclick.

Each action posts to its endpoint (`reject_question`, `mark_question_answered`, `approve_question`),
which checks moderator permission, resolves the question through `accessible_to`, applies the state
change, and re-renders the list fragment. A non-moderator hitting these endpoints gets a permission
error.

### Django admin

`QuestionAdmin` lists the content preview, talk, author display name, vote count, status, a "has
answers" boolean, and the creation date. It offers:

- **Bulk actions**: approve, reject, and mark-as-answered across selected questions.
- **Filters** by status, creation date, and talk title; **search** across content, author fields,
    and talk title.
- An inline editor for `Answer` rows, so an organizer can post an official answer directly from the
    question page.

The changelist annotates vote count and answer existence in the query to avoid an N+1 over many
questions.

## HTMX flow

The Q&A page is almost entirely HTMX:

- The ask form posts and swaps the success area, then refreshes the list.
- Vote, edit, delete, and every moderation button target `#question-list` and swap it out.
- The list polls every 10 seconds (`hx-trigger="every 10s"`) using a morph swap, so new questions
    and vote changes from other attendees appear live without losing the user's scroll position or
    focus.
- The active `status_filter` is threaded through every request via `hx-vals`, so an action taken
    while viewing "Answered" returns to the same filtered view.

## Content caps and author display

Both `Question.content` and `Answer.content` are capped at 2000 characters. The cap is enforced at
the model field and by the generated forms on the create and edit paths, so a logged-in attendee
cannot store a multi-megabyte body that would then be re-rendered to every viewer of the page.

Authors are shown by an obfuscated display label (`display_name`), or "Anonymous" when the question
has no associated user. Moderators acting in the admin can still see the full author email for
accountability.
