"""
Conference talk management module for the event talks site.

This module provides unit test for utils.url.
"""

from utils.url import add_query_param


class TestURL:
    """TestURL implements unit tests for all utils.url."""

    def test_add_query_param_change_url(self) -> None:
        """Test if add_query_param adds a new query param accordingly."""
        url = "http://abc.com"

        new_url = add_query_param(url, "new_param", "new_value")

        assert "?new_param=new_value" in new_url

        new_url = add_query_param(new_url, "new_param_2", "new_value_2")

        assert "&new_param_2=new_value_2" in new_url
