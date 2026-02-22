"""
Avatar caching (memory + disk) and async prefetch for speaker photos.

Provides a two-tier cache (``AVATAR_CACHE`` in-memory dict + on-disk files
under ``MEDIA_ROOT/avatars/``) and an async prefetch routine that warms the
cache before image generation begins.
"""

# ruff: noqa: BLE001
import asyncio
import hashlib
import warnings
from pathlib import Path
from typing import TYPE_CHECKING

import httpx
from django.conf import settings
from pytanis.pretalx.models import State


if TYPE_CHECKING:
    from collections.abc import Sequence

    from pytanis.pretalx.models import Submission

    from talks.management.commands._pretalx.context import ImportContext


# ---------------------------------------------------------------------------
# In-memory avatar cache
# ---------------------------------------------------------------------------

#: Module-level in-memory cache mapping avatar URL -> raw image bytes.
AVATAR_CACHE: dict[str, bytes] = {}


# ---------------------------------------------------------------------------
# Cache path helpers
# ---------------------------------------------------------------------------


def _url_to_cache_path(cache_dir: Path, url: str, ext: str = "webp") -> Path:
    """Return on-disk cache path for *url*, hashed with SHA-256 to avoid filename issues."""
    h = hashlib.sha256(url.encode("utf-8")).hexdigest()
    return cache_dir / f"{h}.{ext}"


def get_avatar_cache_dir() -> Path:
    """Return ``MEDIA_ROOT/avatars/`` as a :class:`~pathlib.Path`."""
    return Path(settings.MEDIA_ROOT) / "avatars"


# ---------------------------------------------------------------------------
# Read / write helpers
# ---------------------------------------------------------------------------


def get_cached_avatar_bytes(cache_dir: Path, url: str) -> bytes | None:
    """Look up *url* in the memory cache, then disk. Hydrate memory on disk hit."""
    data = AVATAR_CACHE.get(url)
    if data is not None:
        return data

    path = _url_to_cache_path(cache_dir, url)
    if path.exists():
        try:
            data = path.read_bytes()
        except Exception as exc:
            warnings.warn(f"Failed to read avatar from disk cache: {exc!s}", stacklevel=2)
        else:
            AVATAR_CACHE[url] = data
            return data
    return None


def save_avatar_bytes(cache_dir: Path, url: str, data: bytes) -> None:
    """Write *data* to disk and memory cache. Warns but does not raise on I/O errors."""
    path = _url_to_cache_path(cache_dir, url)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    except Exception as exc:
        warnings.warn(f"Avatar cache write failed: {exc!s}", stacklevel=2)
    AVATAR_CACHE[url] = data


# ---------------------------------------------------------------------------
# Download helpers
# ---------------------------------------------------------------------------


def download_avatar_bytes_sync(url: str, request_timeout: float = 15) -> bytes | None:
    """Download *url* synchronously via :mod:`httpx`. Return ``None`` on any HTTP error."""
    try:
        resp = httpx.get(url, timeout=request_timeout)
        resp.raise_for_status()
    except httpx.HTTPError:
        return None
    else:
        return resp.content


async def _download_avatar_bytes_async(
    client: httpx.AsyncClient,
    url: str,
    request_timeout: float = 15,
) -> bytes | None:
    """Async counterpart of :func:`download_avatar_bytes_sync` sharing the given *client*."""
    try:
        resp = await client.get(url, timeout=request_timeout)
        resp.raise_for_status()
    except httpx.HTTPError:
        return None
    else:
        return resp.content


# ---------------------------------------------------------------------------
# Async prefetch
# ---------------------------------------------------------------------------


async def _prefetch_avatar_urls(urls: set[str], cache_dir: Path, concurrency: int = 8) -> None:
    """Download all *urls* concurrently (bounded by *concurrency*) into both caches."""

    async def _fetch(client: httpx.AsyncClient, url: str) -> None:
        if get_cached_avatar_bytes(cache_dir, url) is not None:
            return
        try:
            data = await _download_avatar_bytes_async(client, url)
            if data is not None:
                save_avatar_bytes(cache_dir, url, data)
        except Exception as exc:
            warnings.warn(f"Avatar prefetch failed: {exc!s}", stacklevel=2)

    limits = httpx.Limits(max_connections=concurrency, max_keepalive_connections=concurrency)
    async with httpx.AsyncClient(limits=limits) as client:
        await asyncio.gather(*(_fetch(client, u) for u in urls))


def prefetch_avatar_urls(urls: set[str], cache_dir: Path, concurrency: int = 8) -> None:
    """Run :func:`_prefetch_avatar_urls` synchronously via :func:`asyncio.run`."""
    asyncio.run(_prefetch_avatar_urls(urls, cache_dir, concurrency))


# ---------------------------------------------------------------------------
# Submission-level prefetch
# ---------------------------------------------------------------------------


def prefetch_avatars_for_submissions(
    submissions: Sequence[Submission],
    ctx: ImportContext,
) -> None:
    """
    Extract unique avatar URLs from accepted/confirmed *submissions* and prefetch.

    No-op when ``ctx.skip_images`` is ``True``.
    """
    if ctx.skip_images:
        return

    urls: set[str] = set()
    for sub in submissions:
        if getattr(sub, "state", None) not in {State.accepted, State.confirmed}:
            continue
        for sp in getattr(sub, "speakers", None) or []:
            url = getattr(sp, "avatar_url", None) or ""
            if url:
                urls.add(url)

    if urls:
        prefetch_avatar_urls(urls, get_avatar_cache_dir())
