from furl import furl


def add_query_param(url: str, key: str, value: str) -> str:
    """Enrich URL add new query params and return the new url."""
    f = furl(url)
    f.add({key: value})
    return f.url
