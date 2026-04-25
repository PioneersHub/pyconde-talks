"""Property-based tests for utils.email_utils using Hypothesis."""

from hypothesis import (
    given,
    strategies as st,
)

from utils.email_utils import _mask_token, hash_email, obfuscate_email


SHA256_HEX_LENGTH = 64

# ---------------------------------------------------------------------------
# _mask_token properties
# ---------------------------------------------------------------------------

_letters = st.characters(whitelist_categories=["L"])


class TestMaskTokenProperties:
    """Hypothesis property-based tests for _mask_token."""

    @given(st.text(min_size=0, max_size=200))
    def test_never_longer_than_input(self, token: str) -> None:
        """Masked output is never longer than input (after stripping whitespace)."""
        result = _mask_token(token)
        assert len(result) <= max(len(token.strip()), 5)  # "a***z" = 5 max

    @given(st.text(min_size=3, max_size=200, alphabet=_letters))
    def test_preserves_first_and_last_char(self, token: str) -> None:
        """For inputs >= 3 chars, the first and last characters are kept."""
        result = _mask_token(token)
        assert result[0] == token[0]
        assert result[-1] == token[-1]

    @given(st.text(min_size=1, max_size=200))
    def test_never_leaks_middle_chars(self, token: str) -> None:
        """No character from positions 1..-2 appears in the output."""
        stripped = token.strip()
        if len(stripped) <= 2:  # noqa: PLR2004
            return  # first/last overlap, nothing to check
        result = _mask_token(token)
        visible_middle = result[1:-1]
        assert all(ch == "*" for ch in visible_middle)


# ---------------------------------------------------------------------------
# obfuscate_email properties
# ---------------------------------------------------------------------------

_letters_and_digits = st.characters(whitelist_categories=["L", "N"])
_email_local = st.text(min_size=1, max_size=30, alphabet=_letters_and_digits)
_email_domain = st.text(min_size=1, max_size=20, alphabet=_letters_and_digits)
_email_tld = st.sampled_from(["com", "org", "net", "de", "io", "co.uk"])
_email_strategy = st.builds(
    lambda loc, dom, tld: f"{loc}@{dom}.{tld}",
    _email_local,
    _email_domain,
    _email_tld,
)


class TestObfuscateEmailProperties:
    """Hypothesis property-based tests for obfuscate_email."""

    @given(_email_strategy)
    def test_preserves_at_sign(self, email: str) -> None:
        """Obfuscated email always contains exactly one @."""
        result = obfuscate_email(email)
        assert result.count("@") == 1

    @given(_email_strategy)
    def test_preserves_tld(self, email: str) -> None:
        """The TLD is preserved unchanged in the output."""
        tld = email.rsplit(".", maxsplit=1)[-1]
        result = obfuscate_email(email)
        assert result.endswith(f".{tld}")

    @given(_email_strategy)
    def test_local_part_not_readable(self, email: str) -> None:
        """The full local part (>2 chars) must not appear verbatim in the output."""
        local = email.split("@", maxsplit=1)[0]
        result = obfuscate_email(email)
        masked_local = result.split("@")[0]
        if len(local) > 2:  # noqa: PLR2004
            assert masked_local != local

    @given(_email_strategy)
    def test_domain_part_not_readable(self, email: str) -> None:
        """The SLD must not appear verbatim in the output."""
        domain = email.split("@")[1]
        sld = domain.split(".")[-2]
        result = obfuscate_email(email)
        masked_domain = result.split("@")[1]
        masked_sld = masked_domain.rsplit(".", maxsplit=1)[0].split(".")[-1]
        if len(sld) > 2:  # noqa: PLR2004
            assert masked_sld != sld


# ---------------------------------------------------------------------------
# hash_email properties
# ---------------------------------------------------------------------------


class TestHashEmailProperties:
    """Hypothesis property-based tests for hash_email."""

    @given(st.emails())
    def test_deterministic(self, email: str) -> None:
        """Same input always gives same hash."""
        assert hash_email(email) == hash_email(email)

    @given(st.emails())
    def test_fixed_length_hex(self, email: str) -> None:
        """SHA-256 produces a 64-char hex string."""
        result = hash_email(email)
        assert len(result) == SHA256_HEX_LENGTH
        assert all(c in "0123456789abcdef" for c in result)

    @given(st.emails(), st.emails())
    def test_different_inputs_different_hashes(self, email_a: str, email_b: str) -> None:
        """Different emails produce different hashes (collision-free for practical purposes)."""
        if email_a != email_b:
            assert hash_email(email_a) != hash_email(email_b)
