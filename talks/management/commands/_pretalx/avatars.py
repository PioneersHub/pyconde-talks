"""Avatar caching (memory + disk) and async prefetch for speaker photos."""

# ruff: noqa: BLE001
import asyncio
import hashlib
import warnings
from pathlib import Path

import httpx
from django.conf import settings


# ---------------------------------------------------------------------------
# In-memory avatar cache
# ---------------------------------------------------------------------------

AVATAR_CACHE: dict[str, bytes] = {}


# ---------------------------------------------------------------------------
# Cache path helpers
# ---------------------------------------------------------------------------


def _url_to_cache_path(cache_dir: Path, url: str) -> Path:
    """Return on-disk cache path for a URL using SHA-256 hash."""
    h = hashlib.sha256(url.encode("utf-8")).hexdigest()
    return cache_dir / f"{h}.img"


def get_avatar_cache_dir() -> Path:
    """Return the on-disk avatar cache directory path."""
    return Path(settings.MEDIA_ROOT) / "avatars"


# ---------------------------------------------------------------------------
# Read / write helpers
# ---------------------------------------------------------------------------


def get_cached_avatar_bytes(cache_dir: Path, url: str) -> bytes | None:
    """Return cached bytes from memory or disk (and hydrate memory if from disk)."""
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
    """Persist avatar bytes to disk and memory cache."""
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
    """Download avatar bytes synchronously; return ``None`` on failure."""
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
    """Download avatar bytes asynchronously; return ``None`` on failure."""
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
    """Prefetch avatar URLs into memory and disk cache using :class:`httpx.AsyncClient`."""

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
    """Run the async avatar prefetch synchronously via :func:`asyncio.run`."""
    asyncio.run(_prefetch_avatar_urls(urls, cache_dir, concurrency))
