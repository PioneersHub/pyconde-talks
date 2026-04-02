"""Tests for users.validators."""

import pytest
from django.core.exceptions import ValidationError

from users.validators import validate_display_name


class TestValidateDisplayName:
    """Tests for the validate_display_name validator."""

    @pytest.mark.parametrize(
        "value",
        [
            pytest.param("", id="empty"),
            pytest.param("   ", id="whitespace-only"),
            pytest.param("ab", id="min-length"),
            pytest.param("\u59d3\u540d", id="chinese-name"),
            pytest.param("Ana", id="three-chars"),
            pytest.param("Jane Doe", id="full-name"),
            pytest.param("user123", id="alphanumeric"),
            pytest.param("O'Brien", id="apostrophe"),
            pytest.param("Dr. X Y", id="dots-and-spaces"),
        ],
    )
    def test_valid(self, value: str) -> None:
        """Accept blank values and names with at least 2 visible characters."""
        validate_display_name(value)  # should not raise

    @pytest.mark.parametrize(
        ("value", "expected_code"),
        [
            pytest.param("A", "min_length", id="single-char"),
            pytest.param("Hi\u200b", "invisible_chars", id="zero-width-space"),
            pytest.param("\u200bAna", "invisible_chars", id="leading-zero-width-space"),
            pytest.param("Ana\u00ad", "invisible_chars", id="soft-hyphen"),
            pytest.param("Jo\u2060hn", "invisible_chars", id="word-joiner"),
            pytest.param("...", "no_content", id="only-dots"),
            pytest.param("!!!", "no_content", id="only-punctuation"),
            pytest.param("   ---   ", "no_content", id="dashes-and-spaces"),
            pytest.param("@#$%", "no_content", id="only-symbols"),
        ],
    )
    def test_invalid(self, value: str, expected_code: str) -> None:
        """Reject names that are too short, invisible, or decorative-only."""
        with pytest.raises(ValidationError) as exc_info:
            validate_display_name(value)
        assert exc_info.value.code == expected_code
