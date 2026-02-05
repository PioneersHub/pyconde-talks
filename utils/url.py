"""Utility functions for URL manipulation."""

from typing import cast

from furl import furl


def add_query_param(url: str, key: str, value: str) -> str:
    """Enrich the URL adding a new query param and return the new url."""
    f = furl(url)
    f.add({key: value})
    return cast("str", f.url)
