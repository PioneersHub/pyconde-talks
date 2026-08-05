---
icon: lucide/eye
---

# Event visibility and public access

An event's audience changes over its life. While it runs, only ticket holders should reach the
recordings. Months later the videos go up on YouTube and there is nothing left to protect. The
programme itself, titles, abstracts and speakers, was published on Pretalx before the event even
started.

`Event.visibility` and `Event.qa_mode` let one event move through those stages from the admin, with
no code change.

Source:
[`events/models.py`](https://github.com/PioneersHub/pyconde-talks/blob/main/events/models.py),
[`talks/models.py`](https://github.com/PioneersHub/pyconde-talks/blob/main/talks/models.py).

## The three visibility states

| State           | Anonymous visitor sees                | Recordings          |
| --------------- | ------------------------------------- | ------------------- |
| `HIDDEN`        | nothing                               | ticket holders only |
| `SCHEDULE_ONLY` | titles, abstracts, speakers, schedule | ticket holders only |
| `PUBLIC`        | everything                            | anyone              |

New events default to `HIDDEN`, which is the behaviour the site had before this existed. Opening an
event up is always a deliberate act.

A typical event walks the table top to bottom: hidden while the programme is still moving,
schedule-only once it is announced, and public a few months after the videos are on YouTube.

### Who sees what

Listing access is decided in one place, `TalkQuerySet.accessible_to`. Everyone except superusers
sees talks on **active** events only, and within those the union of two sets: talks on events they
hold a ticket for, and talks on events that are not hidden.

Deactivating an event (`is_active = False`) therefore takes it off the site for everyone, ticket
holders included. It is already gone from the event picker, so anything still reachable would only
be reachable by whoever kept a direct link. Superusers keep seeing it, which is how it gets fixed.

The case worth stating plainly is that **being logged in is not itself access**. A visitor with an
account but no ticket for a hidden event sees exactly what an anonymous visitor sees. Logging in
adds your own events to the public set; it does not unlock anything else.

### Recordings are a separate decision

Whether a talk is *listed* and whether its recording *plays* are different questions, which is the
entire point of `SCHEDULE_ONLY`. Videos are plain YouTube and Vimeo URLs rendered into an iframe, so
withholding a recording means not putting the URL in the page at all.

The gate defaults to closed. A view has to unlock it explicitly, through `allow_videos_for` for a
single talk or `TalkQuerySet.with_video_access` for a list. A view that forgets renders a missing
player: annoying and obvious. The alternative, guarding each template, would fail the other way, and
one forgotten template would leak every recording on the site.

`Talk.has_recording()` answers "does a recording exist" without reference to a viewer. It is for the
dashboard counter and the admin column, which report on the catalogue and must not show different
totals to different people.

### Holding back a single talk

`Talk.hide` takes one talk out of every non-superuser view, whatever its event's visibility and even
for ticket holders. Use it for an embargoed or cancelled session.

## Registration

For a `PUBLIC` event the ticket-validation API is skipped and any valid email may register. The
content is already open, so the only things a login still buys are Q&A, ratings and saved talks;
refusing to create accounts would not protect anything, it would just stop people using those.

A deactivated account stays deactivated. The `is_active` check runs before the visibility check, so
making an event public does not readmit anyone who was removed.

### Who got in without a ticket

`EventAccessGrant` records *how* each membership was granted: `ticket`, `open_registration`,
`discord_role` or `transfer`. `CustomUser.events` only records *that* someone has access, and once
open registration existed those two stopped being the same thing.

This matters before taking an event back off public visibility. Without the record, the accounts let
in with no ticket check are indistinguishable from real ticket holders, so they would silently keep
their access with no way to find them again. The user admin shows the grants read-only under *How
event access was granted*.

Every login flow goes through `grant_event_access`, which writes both at once. An existing grant
keeps its original source, so signing in again does not rewrite someone's history. A membership with
no grant row was added by hand in the admin.

Because account creation is then unauthenticated, `request_login_code` carries a per-IP ceiling
(`LOGIN_CODE_IP_RATE_LIMIT`) on top of the per-email limit. The per-email key alone does not bound a
script working through a list of addresses. That ceiling is deliberately loose: the venue's
attendees share one NAT address, so it has to sit well above the opening-session rush.

## What stays behind a login

Q&A, ratings and saved talks require an account at **every** visibility level, public included.
Moderating is volunteer work, so opening an event's recordings does not also open its Q&A.

Asking and voting need more than an account: they need a relationship with the event, meaning a
ticket for it, or an event that is public and therefore open to anyone anyway. Reading the thread
only needs the talk to be listed. Without that split, a ticket for last year's public archive was
enough to post into the Q&A of the conference running right now, which is where moderator attention
is scarcest. Staff and superusers are exempt, because they are the ones moderating.

Those views declare the requirement themselves rather than relying on the URL configuration, and
`talks/tests/test_access_policy.py` pins the split: every closed endpoint is checked against a
public event, so "public event" can never come to imply "public Q&A" by accident.

Anonymous visitors can still save talks. Those bookmarks live in the browser and are folded into the
account on the next login; see [Talks](talks.md).

## Q&A modes

`Event.qa_mode` controls the Q&A independently of visibility, because how much moderator attention
is available changes over an event's life too.

| Mode             | New questions      | Existing questions     |
| ---------------- | ------------------ | ---------------------- |
| `OPEN` (default) | appear immediately | visible                |
| `MODERATED`      | held for approval  | visible                |
| `FROZEN`         | refused            | visible, still votable |
| `DISABLED`       | refused            | hidden; the page 404s  |

`FROZEN` and `DISABLED` close editing as well as asking. An edit replaces the body wholesale, so
leaving it open would make editing the way to post new content after a freeze.

A question held for review is visible to its author and to moderators, nobody else. The author half
matters: otherwise posting into a moderated Q&A is indistinguishable from the post being dropped.

Editing an already-published question runs the same decision again, as if it had just been asked: on
a moderated event it goes back into the queue, and its votes are reset to the author's own, because
those votes were cast on the previous wording. The edit form says both things before you commit. Nor
does answering a held question publish it: only a moderator approving it does that.

`DISABLED` yields nothing even to moderators. The question list polls every ten seconds, so a stale
tab would otherwise keep serving content after the switch was flipped.

## Anti-spam

Open registration means anyone can post, so an open Q&A has several floors under it. All of them
hold a question for review rather than rejecting it, so a false positive costs the asker a delay
rather than their question.

- **Link heuristics**
    ([`talks/spam.py`](https://github.com/PioneersHub/pyconde-talks/blob/main/talks/spam.py)).
    Deliberately conservative: a single link never flags, because "how does this compare to
    `https://scikit-learn.org`?" is an ordinary question and a rule that fires on it would fill the
    queue with noise until moderators stopped reading it. Two or more links, a shortener, a
    messaging platform named next to an actual handle or phone number, or one link alongside
    shouting will flag.

    Naming a messaging platform on its own does not, and neither does a dotted module path such as
    `scipy.io`: both are ordinary things to ask about at a Python conference. The check runs on edit
    as well as create, and for every published status, or the way past it would be to post something
    innocuous and edit the links in afterwards.

    Obfuscated links (`hxxp://`, `spam dot com`, `spam[dot]com`) are normalized before the links are
    counted, shouting is measured across short words as well as long ones, an earnings pitch counts
    as a signal, and a word mixing Latin with another alphabet is treated as a homoglyph swap.

    Deliberately not a library. Akismet would mean an API key, a round trip to a third party on a path
    that must not block the Q&A, and sending attendees' question text off site, all trained on
    English blog comments when half the input here is German.

- **Rate limiting**
    ([`talks/ratelimit.py`](https://github.com/PioneersHub/pyconde-talks/blob/main/talks/ratelimit.py)).
    Five questions per talk per ten minutes and twenty per hour overall, per account and never per
    IP. Moderators are exempt. The allowance is claimed with an atomic increment before the content
    is validated, so a burst of concurrent posts cannot all pass a check none of them had counted
    yet, and refunded if the submission turns out not to be storable, so a rejected draft does not
    cost the author part of their quota.

- **Turnstile**
    ([`utils/turnstile.py`](https://github.com/PioneersHub/pyconde-talks/blob/main/utils/turnstile.py)).
    Optional. With either key unset it is skipped entirely, so dev and CI need no configuration.
    It fails open when Cloudflare is unreachable: losing the captcha for a few minutes beats
    attendees being unable to ask anything during someone else's outage.

The rate limiter counts in the cache, so it is only as global as the cache backend. In production
that is Redis; on the per-process default the allowance is really per worker. See
[Configuration](../getting-started/configuration.md).

## Turning an event public

1. Update the video links, with `update_video_links` (Vimeo) or `update_youtube_links` (a JSON map
    of Pretalx codes to YouTube IDs).
2. Set **Visibility** to *Public* in the event admin.
3. Consider the Q&A. If nobody is watching the queue any more, set **Q&A mode** to *Frozen*, which
    keeps the thread readable, or *Moderated* if you want to keep taking questions.

Nothing else is needed. Registration opens automatically, and the recordings become playable without
an account.
