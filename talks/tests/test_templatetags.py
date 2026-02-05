"""Tests for template tags: highlight, stat_tags, svg_tags, time_filters."""
# ruff: noqa: PLR2004

from pathlib import Path

from django.test import override_settings

from talks.templatetags.highlight import _compile_pattern, highlight
from talks.templatetags.stat_tags import stat_card
from talks.templatetags.svg_tags import svg
from talks.templatetags.time_filters import format_seconds


# ---------------------------------------------------------------------------
# highlight
# ---------------------------------------------------------------------------
class TestHighlight:
    """Tests for the highlight template filter."""

    def test_highlight_match(self) -> None:
        """Wrap matching terms in <mark> tags to highlight search results."""
        result = highlight("Hello world", "world")
        assert "<mark" in result
        assert "world" in result

    def test_highlight_no_match(self) -> None:
        """Return the text unchanged when the query does not match."""
        result = highlight("Hello world", "python")
        assert "<mark" not in result
        assert result == "Hello world"

    def test_highlight_empty_text(self) -> None:
        """Return an empty string when the input text is empty."""
        assert highlight("", "query") == ""

    def test_highlight_empty_query(self) -> None:
        """Return the text unchanged when the query is empty."""
        assert highlight("Hello", "") == "Hello"

    def test_highlight_none_text(self) -> None:
        """Return an empty string when the input text is None."""
        assert highlight(None, "query") == ""  # type: ignore[arg-type]

    def test_highlight_none_query(self) -> None:
        """Return the text unchanged when the query is None."""
        assert highlight("Hello", None) == "Hello"

    def test_highlight_multiple_terms(self) -> None:
        """Highlight each term independently when the query contains multiple words."""
        result = highlight("foo bar baz", "foo baz")
        assert result.count("<mark") == 2

    def test_highlight_whitespace_only_query(self) -> None:
        """Return the text as-is when all query terms are whitespace."""
        assert highlight("Hello", "   ") == "Hello"

    def test_compile_pattern_empty(self) -> None:
        """Return None when the term list is empty or contains only whitespace."""
        assert _compile_pattern([]) is None
        assert _compile_pattern(["", "  "]) is None

    def test_compile_pattern_valid(self) -> None:
        """Compile a regex that matches any of the given terms."""
        pattern = _compile_pattern(["hello", "world"])
        assert pattern is not None
        assert pattern.search("hello world")


# ---------------------------------------------------------------------------
# stat_tags
# ---------------------------------------------------------------------------
class TestStatCard:
    """Verify the stat_card inclusion tag returns title and value context."""

    def test_stat_card_returns_context(self) -> None:
        """Return a dict with title and numeric value for template rendering."""
        result = stat_card("Total Talks", 42)
        assert result == {"title": "Total Talks", "value": 42}

    def test_stat_card_string_value(self) -> None:
        """Accept string values in addition to numeric ones."""
        result = stat_card("Status", "Active")
        assert result["title"] == "Status"
        assert result["value"] == "Active"


# ---------------------------------------------------------------------------
# svg_tags
# ---------------------------------------------------------------------------
class TestSvgTag:
    """Verify the svg tag reads SVG files and injects optional CSS classes."""

    def test_svg_file_not_found(self) -> None:
        """Return an empty string when the SVG file does not exist."""
        result = svg("nonexistent_icon")
        assert result == ""

    @override_settings(BASE_DIR=Path("/tmp/test_svg_project"))  # noqa: S108
    def test_svg_with_css_class(self, tmp_path: Path) -> None:
        """Inject a class attribute into the SVG root element."""
        svg_dir = tmp_path / "svg"
        svg_dir.mkdir()
        svg_file = svg_dir / "test.svg"
        svg_file.write_text('<svg viewBox="0 0 24 24"><path d="M0 0"/></svg>')

        with override_settings(BASE_DIR=tmp_path):
            result = svg("test", "h-4 w-4")
            assert 'class="h-4 w-4"' in result

    @override_settings(BASE_DIR=Path("/tmp/test_svg_project"))  # noqa: S108
    def test_svg_without_css_class(self, tmp_path: Path) -> None:
        """Return the raw SVG markup without adding a class attribute."""
        svg_dir = tmp_path / "svg"
        svg_dir.mkdir()
        svg_file = svg_dir / "icon.svg"
        svg_file.write_text('<svg viewBox="0 0 24 24"><path d="M0 0"/></svg>')

        with override_settings(BASE_DIR=tmp_path):
            result = svg("icon")
            assert "<svg" in result
            assert "class=" not in result


# ---------------------------------------------------------------------------
# time_filters
# ---------------------------------------------------------------------------
class TestFormatSeconds:
    """Verify format_seconds converts numeric seconds into h:mm:ss display format."""

    def test_zero(self) -> None:
        """Format zero seconds as '0:00'."""
        assert format_seconds(0) == "0:00"

    def test_minutes_and_seconds(self) -> None:
        """Format seconds into m:ss when under one hour."""
        assert format_seconds(103) == "1:43"

    def test_hours(self) -> None:
        """Include hours when the value is 3600 seconds or more."""
        assert format_seconds(3700) == "1:01:40"

    def test_string_input(self) -> None:
        """Parse a numeric string before formatting."""
        assert format_seconds("3600") == "1:00:00"

    def test_negative(self) -> None:
        """Prefix the result with a minus sign for negative values."""
        assert format_seconds(-75) == "-1:15"

    def test_invalid_string(self) -> None:
        """Fall back to '0:00' when the input cannot be parsed as a number."""
        assert format_seconds("invalid") == "0:00"

    def test_float_input(self) -> None:
        """Truncate fractional seconds before formatting."""
        assert format_seconds(90.5) == "1:30"

    def test_exact_hour(self) -> None:
        """Show exactly '1:00:00' for a precise one-hour value."""
        assert format_seconds(3600) == "1:00:00"

    def test_just_seconds(self) -> None:
        """Format values under one minute as '0:SS'."""
        assert format_seconds(45) == "0:45"
