---
icon: lucide/history
---

# The story

The Conference Talks Website grew out of a real problem at [PyCon DE 2025](https://pycon.de/): how
to give remote attendees a good way to watch the talks. This is the story of how it came to be,
written by its main author and maintainer, Julio Batista Silva.

## Timeline at a glance

| When           | Milestone                                                                       |
| -------------- | ------------------------------------------------------------------------------- |
| January 2025   | The idea is proposed, three months before the conference                        |
| April 2025     | First launch at PyCon DE 2025: streaming with access control                    |
| September 2025 | PyData Berlin 2025: live [Q&A](features/questions.md) added                     |
| 2026           | PyCon DE 2026: heavy refactor, visuals, and talk [ratings](features/ratings.md) |
| Ongoing        | Documentation, security, CI/CD, backups, and a growing group of contributors    |

## The problem: streaming for remote attendees

I was on the video team for PyCon DE 2025, and we wanted to improve the experience for remote
attendees. Our setup was simple: typically one stream per room per day, sometimes split into a
morning and an afternoon stream. We then shared the links on Discord.

That worked for watching live, but it was painful for anyone who wanted to go back to an earlier
session. To rewatch a talk you had to look up which room it was in on
[Pretalx](https://pretalx.com/), find the matching stream link buried in the Discord chat, and then
manually scrub through hours of video to find the moment your talk began. On top of that, a raw link
can be copied and shared with anyone, so there was no real access control.

## The idea: a CUE sheet for conference streams

In a planning meeting on 23 January 2025, exactly three months before the conference opened on 23
April, conference and steering-committee chair Alexander Hendorf suggested an idea that shaped the
whole project.

!!! quote "The seed of the idea"

    Give the live streams something like a CUE sheet, the way audio CDs index their tracks: a simple
    table where you can find which recording a talk is in and jump straight to the second it starts.

That single image, a CUE sheet for conference video, became the core of the app: link every talk to
the exact moment it begins in the right recording, so an attendee never has to hunt through Discord
and scrub through hours of footage again.

## The constraints

The idea was clear. The constraints made it interesting:

- **Authentication was required.** Remote tickets are paid. The talks are made public a few months
    after the conference, but while they are fresh it would not be fair to paying attendees to leave
    the live streams open to everyone. See [Authentication](getting-started/authentication.md) for
    how this ended up working.
- **It had to scale.** We expected close to 1500 in-person attendees and around 500 remote ones.
- **We cared about privacy and security.** We wanted to handle personal data carefully and, if at
    all possible, avoid storing passwords at all.
- **We had almost no time and no budget.** It was built on volunteer time, mostly nights and
    weekends, in under three months. So it had to stay simple.

## Why Django

This being a Python conference, it felt right to build the solution in Python. I remembered Django's
"batteries included" philosophy and suggested it. My own history with Django was, let us say, not
recent: I had last used it for my personal website around 2011, fourteen years earlier, back when I
was still writing Python 2.

So I started researching what modern Django can do, and it was a pleasant surprise. A proof of
concept came together quickly. [django-allauth](https://docs.allauth.org/) solved the passwordless
authentication cleanly, so we never had to store passwords for regular users. We also had a head
start because the Discord bot built earlier had already solved several hard parts:

1. validating that someone holds a valid ticket, by querying the ticketing platform's API,
2. pulling talks and schedules from Pretalx, and
3. where to host it, since we already had a Linux server.

## Three months of nights and weekends

From there it was steady, daily work in my free time to turn the proof of concept into something
pleasant to use. Most of the friction came from working with Vimeo, where getting the embedding and
the per-talk seek points right took real effort.

Along the way I learned enough modern Django to mentor at [Django Girls](https://djangogirls.org/)
Darmstadt, which happened the day before PyCon DE 2025 started.

## Launch: PyCon DE 2025

The project was a success. It handled all the users without trouble, and we had no incidents during
the conference. Just as valuable, it came back with positive feedback and a list of suggestions for
new features, which set the direction for everything that followed.

## PyData Berlin 2025: live Q&A

In 2025 I was also an organizer at [PyData Berlin](https://pydata.org/berlin2025) (1 to 3 September
2025), and we decided to adapt the same codebase to run that event too.

The headline feature this time was [Q&A](features/questions.md). Theodore Meynard, an organizer of
PyData Berlin, submitted the first draft as a pull request, and I then worked to integrate it well
with the rest of the site. This was a real upgrade. Before, we used a separate paid service for
audience questions, which cost money and kept questions split per room. Attendees had to open a
different page to ask anything, and questions from the previous session were still sitting there, so
a moderator had to delete them by hand, which erased the history. Building Q&A directly into the app
saved money and made the whole flow simpler and better.

## PyCon DE 2026: ratings and a big refactor

In 2026 I was again an organizer at PyCon DE, this time on the Diversity Committee, and I kept
working on the app. I did a fair amount of heavy refactoring and improved the visuals, the admin
pages, and the underlying logic, alongside several new features.

One of those features was talk [ratings](features/ratings.md): attendees can rate talks and leave
private comments. Theodore Meynard added the first version, and I adapted it, reworking some of the
logic and visuals, and finished it in time for the conference.

In 2026 we also began work on live captioning, the real-time transcription of talks as they happen.
For me this sits naturally alongside my diversity and inclusion work: captions help people who are
deaf or hard of hearing, attendees following along in a second language, and anyone in a room where
the audio is hard to catch. The work is still under way, and it is one of the additions I am most
excited about.

## Maturing the project

After PyCon DE 2026 I kept maintaining the website, shifting focus toward the things that keep a
project healthy for the long run:

- [documentation](development/documentation.md) (including the site you are reading now),
- security and code quality,
- [CI/CD](deployment/ci-cd.md) and automated [deployment](deployment/index.md),
- database [backups](deployment/operations.md), and
- implementing the suggestions that keep arriving as feedback.

## A growing community

Other organizers have started showing interest in contributing code, which is exactly where I hope
this goes. Special thanks to Gaweng Tan for starting the first implementation of session-chair
planning. That is another job we used to pay an external service for, one that was not very user
friendly and had some annoying bugs. Bringing it into the app makes a lot of sense: it is easier for
organizers, it does not require creating yet another account somewhere else, and it removes one more
recurring cost. That is money better spent elsewhere, for example on the Financial Aid program.

The project stands on the work of
[everyone who has contributed](https://github.com/PioneersHub/pyconde-talks/graphs/contributors).

## Get involved

We want more contributors. If any of this resonates with you, whether you are an organizer, a Python
developer, or just curious, the [contributing guide](contributing.md) is the place to start.

## Run it for your own conference

The app was built from the start to serve [more than one event](architecture/index.md) from a single
installation, which is how it already runs both PyCon DE and PyData Berlin. If you organize a
conference and think it could help your attendees, I am happy to help you adapt it for your event.
Just get in touch: the easiest way is to open an issue on the
[project repository](https://github.com/PioneersHub/pyconde-talks/issues).
