"""Tests for utils.email_utils."""

import pytest

from utils.email_utils import _mask_token, obfuscate_email


class TestMaskToken:
    """Tests for the _mask_token helper function."""

    @pytest.mark.parametrize(
        ("token", "expected"),
        [
            pytest.param("", "", id="empty"),
            pytest.param("a", "*", id="single-char"),
            pytest.param("ab", "a*", id="two-chars"),
            pytest.param("abc", "a***c", id="three-chars"),
            pytest.param("john", "j***n", id="four-chars"),
            pytest.param("alexander", "a***r", id="long"),
            pytest.param("  ab  ", "a*", id="whitespace-stripped"),
        ],
    )
    def test_mask_token(self, token: str, expected: str) -> None:
        """Mask the middle of a token, keeping only first and last characters."""
        assert _mask_token(token) == expected


class TestObfuscateEmail:
    """Tests for the obfuscate_email function."""

    @pytest.mark.parametrize(
        ("email", "expected"),
        [
            pytest.param("", "", id="empty"),
            pytest.param("notanemail", "n***l", id="no-at-sign"),
            pytest.param("a@b.com", "*@*.com", id="single-char-parts"),
            pytest.param("ab@xy.org", "a*@x*.org", id="two-char-parts"),
            pytest.param("john.doe@example.com", "j***e@e***e.com", id="typical"),
            pytest.param("user@mail.example.co.uk", "u***r@c*.uk", id="subdomains"),
            pytest.param("  jane@test.org  ", "j***e@t***t.org", id="whitespace-stripped"),
        ],
    )
    def test_obfuscate_email(self, email: str, expected: str) -> None:
        """Obfuscate an email by masking local and domain parts."""
        assert obfuscate_email(email) == expected

    def test_single_label_domain(self) -> None:
        """Keep the domain intact (no dot) when there is only one label."""
        result = obfuscate_email("user@localhost")
        local, domain = result.split("@")
        assert "." not in domain
        assert local == "u***r"
