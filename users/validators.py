"""Validators for user-related fields."""

import unicodedata

from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _


MIN_DISPLAY_NAME_LENGTH = 2

# Unicode categories that are invisible or non-printable.
# See https://www.unicode.org/reports/tr44/#General_Category_Values
_INVISIBLE_CATEGORIES = frozenset({"Cf", "Cc", "Co", "Cs", "Zl", "Zp"})


def _strip_invisible(value: str) -> str:
    """Remove invisible Unicode characters from a string."""
    return "".join(ch for ch in value if unicodedata.category(ch) not in _INVISIBLE_CATEGORIES)


def _has_letter_or_digit(value: str) -> bool:
    """Return True if the string contains at least one letter or digit."""
    return any(ch.isalnum() for ch in value)


def validate_display_name(value: str) -> None:
    """
    Validate a user display name.

    Rules (applied only when the field is non-empty):
    - Must be at least 2 visible characters after stripping whitespace and invisible Unicode.
    - Must not consist solely of punctuation, symbols, or whitespace.
    - Must not contain invisible Unicode characters (zero-width spaces, etc.).
    """
    if not value or not value.strip():
        # Blank is allowed (the field is optional).
        return

    stripped = value.strip()

    if stripped != _strip_invisible(stripped):
        raise ValidationError(
            _("Display name must not contain invisible characters."),
            code="invisible_chars",
        )

    visible = _strip_invisible(stripped)
    if len(visible) < MIN_DISPLAY_NAME_LENGTH:
        raise ValidationError(
            _("Display name must be at least %(min_length)d visible characters."),
            code="min_length",
            params={"min_length": MIN_DISPLAY_NAME_LENGTH},
        )

    if not _has_letter_or_digit(stripped):
        raise ValidationError(
            _("Display name must contain at least one letter or digit."),
            code="no_content",
        )
