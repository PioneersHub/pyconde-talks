"""Utility functions for URL manipulation."""

import re
from typing import cast

from furl import furl


# YouTube video IDs are 11 characters of the URL-safe base64 alphabet.
YOUTUBE_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{11}$")

# Path prefixes that carry the video ID in the next segment. ``watch`` is missing on purpose:
# it carries the ID in the ``v`` query parameter instead.
YOUTUBE_ID_PATH_PREFIXES = ("embed", "shorts", "live", "v")

YOUTUBE_EMBED_URL = "https://www.youtube.com/embed/{video_id}"

# Query parameters that must not be carried over into the embed URL: ``v`` and ``t`` are
# translated, the rest are link-sharing noise that means nothing to the embedded player.
YOUTUBE_DROPPED_PARAMS = frozenset({"v", "t", "feature", "si", "pp", "ab_channel"})


def add_query_param(url: str, key: str, value: str) -> str:
    """Enrich the URL adding a new query param and return the new url."""
    f = furl(url)  # type: ignore[operator]
    f.add({key: value})
    return cast("str", f.url)


def youtube_video_id(url: str) -> str:
    """
    Return the video ID *url* points at, or "" when it is not a YouTube video URL.

    Every shape a YouTube link reaches us in is accepted: the short ``youtu.be/<id>`` form, the
    ``watch?v=<id>`` form a human copies from the address bar, and the ``embed`` / ``shorts`` /
    ``live`` / ``v`` paths. The ID has to look like an ID, so an unrelated youtube.com URL (a
    channel page, say) reads as "no video" instead of yielding a nonsense ID.
    """
    if not url:
        return ""

    parsed = furl(url)  # type: ignore[operator]
    host = (parsed.host or "").lower().removeprefix("www.")
    segments = [segment for segment in parsed.path.segments if segment]

    match host, segments:
        case "youtu.be", [short_id, *_]:
            candidate = short_id
        case "youtube.com", [prefix, path_id, *_] if prefix in YOUTUBE_ID_PATH_PREFIXES:
            candidate = path_id
        case "youtube.com", _:
            candidate = str(parsed.args.get("v", "") or "")
        case _:
            return ""

    return candidate if YOUTUBE_ID_PATTERN.match(candidate) else ""


def youtube_embed_url(url: str) -> str:
    """
    Return the framable form of a YouTube *url*, or *url* unchanged when it is not one.

    YouTube serves ``youtu.be`` and ``watch`` URLs with ``X-Frame-Options: SAMEORIGIN``, so a
    page that drops one into an iframe shows an empty box. Only ``/embed/<id>`` may be framed,
    and the IFrame API the player controls are built on needs it too. Links are stored in
    whatever form they arrive in (organizers paste short links, and old rows already hold them),
    so the conversion happens on the way out to the template rather than on the way in.

    Query parameters are carried over, so a stored ``?enablejsapi=1`` survives. A ``t`` offset
    from a shared link becomes the ``start`` parameter the embedded player understands.
    """
    video_id = youtube_video_id(url)
    if not video_id:
        return url

    embed = furl(YOUTUBE_EMBED_URL.format(video_id=video_id))  # type: ignore[operator]
    original = furl(url)  # type: ignore[operator]
    embed.args = {
        key: value for key, value in original.args.items() if key not in YOUTUBE_DROPPED_PARAMS
    }

    start = str(original.args.get("t", "") or "").removesuffix("s")
    if start.isdigit():
        embed.args["start"] = start

    return cast("str", embed.url)
