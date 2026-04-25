"""Property-based tests for users.validators using Hypothesis."""

import unicodedata

import pytest
from django.core.exceptions import ValidationError
from hypothesis import (
    given,
    strategies as st,
)

from users.validators import _INVISIBLE_CATEGORIES, MIN_DISPLAY_NAME_LENGTH, validate_display_name


# Strategy: visible characters (letters, digits, common punctuation)
# Exclude all whitespace-like categories (Z*) since str.strip() removes them,
# plus invisible categories that the validator rejects.
_visible_chars = st.characters(
    whitelist_categories=["L", "N", "P", "S"],
    blacklist_categories=["Cf", "Cc", "Co", "Cs", "Zl", "Zp", "Zs"],
)
_punctuation_only = st.characters(whitelist_categories=["P", "S"])

# cspell:words ufeff
_INVISIBLE_SAMPLES = ["\u200b", "\u200c", "\u200d", "\u2060", "\u00ad", "\ufeff"]


class TestDisplayNameProperties:
    """Hypothesis property-based tests for validate_display_name."""

    @given(st.text(min_size=MIN_DISPLAY_NAME_LENGTH, max_size=100, alphabet=_visible_chars))
    def test_visible_text_with_letter_always_valid(self, name: str) -> None:
        """A string of visible chars with at least one letter/digit never raises."""
        if not any(ch.isalnum() for ch in name):
            return  # skip pure-punctuation; that's the "no_content" path
        validate_display_name(name)  # should not raise

    @given(st.text(alphabet=st.characters(whitelist_categories=["Cf", "Cc"])))
    def test_invisible_only_rejected(self, name: str) -> None:
        """Strings made entirely of invisible characters are rejected or blank."""
        if not name.strip():
            validate_display_name(name)  # blank is allowed
            return
        with pytest.raises(ValidationError):
            validate_display_name(name)

    @given(
        st.text(min_size=2, max_size=50, alphabet=_visible_chars),
        st.sampled_from(_INVISIBLE_SAMPLES),
    )
    def test_injected_invisible_char_rejected(self, base: str, invisible: str) -> None:
        """Inserting an invisible character into a visible name triggers rejection."""
        mid = len(base) // 2
        tainted = base[:mid] + invisible + base[mid:]
        cat = unicodedata.category(invisible)
        if cat in _INVISIBLE_CATEGORIES:
            with pytest.raises(ValidationError) as exc_info:
                validate_display_name(tainted)
            assert exc_info.value.code == "invisible_chars"

    @given(st.text(max_size=1, alphabet=_visible_chars))
    def test_single_visible_char_rejected(self, ch: str) -> None:
        """A single visible character is below MIN_DISPLAY_NAME_LENGTH."""
        if not ch.strip():
            return  # blank is valid
        with pytest.raises(ValidationError) as exc_info:
            validate_display_name(ch)
        assert exc_info.value.code == "min_length"

    @given(st.text(min_size=2, max_size=20, alphabet=_punctuation_only))
    def test_punctuation_only_rejected(self, name: str) -> None:
        """Strings of only punctuation/symbols are rejected as no_content."""
        if not name.strip():
            return
        with pytest.raises(ValidationError) as exc_info:
            validate_display_name(name)
        assert exc_info.value.code == "no_content"
