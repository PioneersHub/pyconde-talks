"""
Lightweight heuristics that hold likely-spam questions for review.

Deliberately conservative. A false positive costs an attendee a few minutes while a moderator
approves their question; a rule that fires often costs the moderators their trust in the queue, and
a queue nobody reads carefully is worse than no queue at all. So every rule here aims at a pattern
that essentially never appears in a real conference question.

The clearest example is links. Advertising is full of them, but so is a good question: "how does
this compare to
https://scikit-learn.org?"
is entirely normal. A single link therefore never flags
on its own. It takes a second signal, or several links, to look like an advert.

Not a library. The usual candidate is Akismet, which means an API key, a per-request round trip to a
third party on a path that must not block the Q&A, and sending attendees' question text off site.
Its training is also aimed at blog comments in English, whereas half the input here is German. A
handful of rules aimed at this specific context is both cheaper and easier to explain to the
moderator who has to trust the queue. ``QA_SPAM_KEYWORDS`` is the escape hatch for a campaign these
rules do not catch.
"""

import re
import unicodedata
from typing import Final

from django.conf import settings


# cspell:ignore wechat tinyurl cutt TLDS verdiene verdienen woche monat


# A written-out link: it has a scheme or a www prefix, so there is no doubt it is one.
_URL_RE: Final = re.compile(r"(?i)\b(?:https?://\S+|www\.\S+)")

# A bare "host.tld" with no scheme. Matched loosely here and filtered by TLD below, rather than
# spelling the TLDs into the pattern: as one alternation it was the most complicated regex in the
# module, and a plain set is both easier to read and easier for an operator to extend.
_BARE_HOST_RE: Final = re.compile(r"(?i)\b[a-z0-9][a-z0-9-]{1,62}\.([a-z]{2,6})\b")

# The TLDs worth counting when there is no scheme. Deliberately narrow: matching every possible
# TLD would flag ordinary prose like "Django 5.0".
#
# ".io" is absent on purpose, though it is a real spam TLD. At a PyData conference "scipy.io",
# "pandas.io", "socket.io" and "tensorflow.io" are module paths people type in ordinary
# questions, and two of them in one question would have been enough to hold it for review.
# Written with a scheme ("https://foo.io") it still counts, through ``_URL_RE``.
_BARE_HOST_TLDS: Final = frozenset(
    {"com", "net", "org", "ru", "cn", "xyz", "top", "shop", "info", "biz", "link", "click"},
)

# Shorteners and messaging links. Real questions ask about the talk; they do not hide a
# destination behind a redirect or route people into a private channel.
#
# Only the link forms, not the bare product names. "Does this work with the Telegram Bot API?"
# and "we ship a WhatsApp integration" are ordinary Python-conference questions, and flagging
# them is exactly the kind of misfire that teaches moderators to stop reading the queue.
_CONTACT_RE: Final = re.compile(
    r"(?i)(\bt\.me/|\bwa\.me/|\bbit\.ly/|\btinyurl\.com/|\bcutt\.ly/|\bt\.me\b)",
)

# A messaging platform named next to a handle or a phone number. That pairing is what an advert
# looks like; the platform name on its own is just a topic.
# Written as concatenated single-line strings rather than one triple-quoted verbose pattern:
# docformatter reads a triple-quoted string in this position as a docstring and reflows it into
# broken syntax, and excluding the file would cost it docstring formatting entirely.
_CONTACT_HANDLE_RE: Final = re.compile(
    r"(?i)"
    r"\b(?:whats\s?app|telegram|wechat|signal)\b"
    r"[\s:,-]{0,10}"
    r"(?:@[a-z0-9_]{3,}|\+?\d[\d\s().-]{6,})",
)

# A long run of capitals reads as shouting. The threshold is high enough to leave the acronyms
# a Python conference is full of alone: API, GPU, SQL, PEP, ORM, ASGI all pass.
_SHOUTING_RE: Final = re.compile(r"\b[A-Z]{8,}\b")

# Shouting spread over several short words ("FREE MONEY CLICK HERE NOW"), which the run rule
# above misses because no single word is long enough. Measured as a ratio so a question that just
# happens to be acronym-heavy stays clear: "I use GPU, SQL and the ORM API daily" is about 40%.
_SHOUTING_RATIO: Final = 0.7
_MIN_LETTERS_FOR_RATIO: Final = 20

# An earnings pitch. Split into three plain patterns rather than one that spans them: a single
# regex needed a variable-length bridge between the verb and the period, which backtracks badly
# on long input. Never a question about a talk, but still only flagged alongside a second signal.
#
# The plural is factored out of the currency alternation ("dollar|euro" plus a trailing "s?", not
# "dollars?|euros?"). Same matches, one nesting level less: spelled per branch it put the pattern
# one point over SonarQube's regex complexity limit.
_MONEY_AMOUNT_RE: Final = re.compile(
    r"(?i)(?:[$€£]\s?\d{3,}|\b\d{3,}\s?(?:usd|eur|dollar|euro)s?\b)",
)
_EARN_VERB_RE: Final = re.compile(r"(?i)\b(?:earn|earning|make|verdiene|verdienen)\b")
# Plain substrings rather than one alternation: as a regex this sat just over the complexity
# limit, and none of it needs regex features once the text is lowercased.
_EARN_TERMS: Final = (
    "per day",
    "per week",
    "per month",
    "from home",
    "guaranteed",
    "pro tag",
    "pro woche",
    "pro monat",
)

# Obfuscations that exist only to get a link past a filter. Normalized before links are counted,
# so "spam dot com" and "hxxp://spam.com" are read as what they are.
#
# The bracketed and bare forms are separate patterns on purpose. One combined pattern with
# optional brackets and ``\s*`` on both sides is ambiguous about where the whitespace belongs and
# backtracks badly on long input. The bare form requires surrounding whitespace, which also keeps
# it from mangling "dotted" into ".ted".
_OBFUSCATION_SUBS: Final = (
    (re.compile(r"(?i)\bh(?:xx|\*\*)p(s?)://"), r"http\1://"),
    (re.compile(r"(?i)[\[(]\s*dot\s*[\])]"), "."),
    (re.compile(r"(?i)\s+dot\s+"), "."),
    (re.compile(r"[\[(]\s*\.\s*[\])]"), "."),
)

# One link is a citation. More than one, in a question, is a list of places to go.
MAX_LINKS: Final = 1


def _configured_keywords() -> list[str]:
    """
    Return the operator-configured spam keywords, lowercased.

    Empty by default. It is a lever for a conference that finds itself under a specific, ongoing
    campaign, so terms can be added without a deploy; keyword lists date badly and are not worth
    maintaining speculatively.
    """
    return [word.lower() for word in getattr(settings, "QA_SPAM_KEYWORDS", []) if word]


def count_links(content: str) -> int:
    """
    Return how many distinct links *content* mentions.

    Written-out links are counted first and then removed, so "https://example.com" is one link
    rather than two: its host would otherwise match the bare-host pattern as well.
    """
    written_out = _URL_RE.findall(content)
    remainder = _URL_RE.sub(" ", content)
    bare = [
        match
        for match in _BARE_HOST_RE.finditer(remainder)
        if match.group(1).lower() in _BARE_HOST_TLDS
    ]
    return len(written_out) + len(bare)


def _deobfuscate(content: str) -> str:
    """
    Undo the tricks used to hide a link from a filter, so the link rules see the real thing.

    Only applied to the copy the patterns run against. The stored question keeps what the author
    typed, since this is a detection aid and not a correction.
    """
    for pattern, replacement in _OBFUSCATION_SUBS:
        content = pattern.sub(replacement, content)
    return content


def _is_shouting(content: str) -> bool:
    """
    Return whether *content* reads as shouting.

    Two ways to qualify: one very long run of capitals, or a message long enough to judge that is
    almost entirely capitals. The second catches "FREE MONEY CLICK HERE NOW", where no single word
    is long enough for the first, without touching a question that is merely acronym-heavy.

    URLs are removed before either test. They are not prose, and their lowercase characters dragged
    the ratio down far enough that "SHOUTING plus a link", the exact pairing the caller is looking
    for, stopped registering as shouting at all.
    """
    prose = _URL_RE.sub(" ", content)
    if _SHOUTING_RE.search(prose):
        return True
    letters = [char for char in prose if char.isalpha()]
    if len(letters) < _MIN_LETTERS_FOR_RATIO:
        return False
    upper = sum(1 for char in letters if char.isupper())
    return upper / len(letters) >= _SHOUTING_RATIO


def _is_money_pitch(content: str, lowered: str) -> bool:
    """Return whether *content* offers money, which no question about a talk does."""
    if _MONEY_AMOUNT_RE.search(content):
        return True
    return bool(_EARN_VERB_RE.search(content)) and any(term in lowered for term in _EARN_TERMS)


def _has_mixed_script_word(content: str) -> bool:
    """
    Return whether any single word mixes Latin letters with another alphabet.

    That is what a homoglyph swap looks like: spelling "Contact" with a Cyrillic capital Es in place
    of the C reads as Latin to a person and slips every literal pattern here. A question quoting
    another script in its own words is fine, because the mixing has to happen inside a single word
    to count.
    """
    for word in re.split(r"\s+", content):
        scripts = {
            "CYRILLIC" if "CYRILLIC" in name else "GREEK" if "GREEK" in name else "LATIN"
            for char in word
            if char.isalpha()
            for name in [unicodedata.name(char, "")]
            if any(script in name for script in ("LATIN", "CYRILLIC", "GREEK"))
        }
        if len(scripts) > 1:
            return True
    return False


def spam_flag_reason(content: str) -> str:
    """
    Return a short reason when *content* looks like spam, or "" when it looks fine.

    The reason is stored on ``Question.flag_reason`` so a moderator can see what caught it and a
    rule that misfires in production can be found in the data rather than guessed at.

    Ordered cheapest and most certain first. Every rule needs either an unambiguous signal of its
    own (several links, a shortener, a homoglyph) or two weaker ones together, so that no single
    ordinary feature of a question is enough on its own.
    """
    normalized = _deobfuscate(content)
    lowered = normalized.lower()
    links = count_links(normalized)
    shouting = _is_shouting(content)

    # A precedence table rather than a chain of early returns: it puts each reason next to the
    # condition that produces it, so the rule set can be read top to bottom as the policy it is.
    rules: tuple[tuple[str, bool], ...] = (
        ("many_links", links > MAX_LINKS),
        (
            "contact_handle",
            bool(_CONTACT_RE.search(normalized) or _CONTACT_HANDLE_RE.search(normalized)),
        ),
        ("mixed_script", _has_mixed_script_word(content)),
        ("money_pitch", _is_money_pitch(normalized, lowered) and bool(links or shouting)),
        # A single link plus shouting is an advert; either alone is not.
        ("link_and_shouting", bool(links) and shouting),
        ("keyword", any(keyword in lowered for keyword in _configured_keywords())),
    )
    for reason, matched in rules:
        if matched:
            return reason
    return ""
