"""
Lightweight heuristics that hold likely-spam questions for review.

Deliberately conservative. A false positive costs an attendee a few minutes while a moderator
approves their question; a rule that fires often costs the moderators their trust in the queue,
and a queue nobody reads carefully is worse than no queue at all. So every rule here aims at a
pattern that essentially never appears in a real conference question.

The clearest example is links. Advertising is full of them, but so is a good question: "how does
this compare to https://scikit-learn.org?" is entirely normal. A single link therefore never
flags on its own - it takes a second signal, or several links, to look like an advert.
"""

import re
from typing import Final

from django.conf import settings


# cspell:ignore wechat tinyurl cutt


# A URL-ish token. The bare-host branch is deliberately narrow: matching every possible TLD
# would flag ordinary prose like "Django 5.0" or a passing mention of "example.io".
_URL_RE: Final = re.compile(
    r"""(?ix)
    \b(
        https?://\S+
        | www\.\S+
        | [a-z0-9][a-z0-9-]{1,62}\.(?:com|net|org|io|ru|cn|xyz|top|shop|info|biz|link|click)\b
    )
    """,
)

# Contact handles and shorteners. Real questions ask about the talk; they do not route people
# to a private channel or hide a destination behind a redirect.
_CONTACT_RE: Final = re.compile(
    r"(?i)\b(whats\s?app|telegram|t\.me|wechat|bit\.ly|tinyurl|cutt\.ly)\b",
)

# A long run of capitals reads as shouting. The threshold is high enough to leave the acronyms
# a Python conference is full of alone: API, GPU, SQL, PEP, ORM, ASGI all pass.
_SHOUTING_RE: Final = re.compile(r"\b[A-Z]{8,}\b")

# One link is a citation. More than one, in a question, is a list of places to go.
MAX_LINKS: Final = 1


def _configured_keywords() -> list[str]:
    """
    Return the operator-configured spam keywords, lowercased.

    Empty by default. It is a lever for a conference that finds itself under a specific,
    ongoing campaign, so terms can be added without a deploy; keyword lists date badly and are
    not worth maintaining speculatively.
    """
    return [word.lower() for word in getattr(settings, "QA_SPAM_KEYWORDS", []) if word]


def spam_flag_reason(content: str) -> str:
    """
    Return a short reason when *content* looks like spam, or "" when it looks fine.

    The reason is stored on ``Question.flag_reason`` so a moderator can see what caught it and
    a rule that misfires in production can be found in the data rather than guessed at.
    """
    links = _URL_RE.findall(content)

    if len(links) > MAX_LINKS:
        return "many_links"
    if _CONTACT_RE.search(content):
        return "contact_handle"
    # A single link plus shouting is an advert; either alone is not.
    if links and _SHOUTING_RE.search(content):
        return "link_and_shouting"

    lowered = content.lower()
    if any(keyword in lowered for keyword in _configured_keywords()):
        return "keyword"

    return ""
