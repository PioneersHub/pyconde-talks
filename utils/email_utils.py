"""Utility functions for handling email addresses."""

import hashlib


def hash_email(email: str) -> str:
    """Create a SHA-256 hash of an email address."""
    return hashlib.sha256(email.encode()).hexdigest()


def _mask_token(s: str) -> str:
    """
    Mask middle of a token: a -> *, ab -> a*, abc+ -> a***c.

    >>> _mask_token("")
    ''
    >>> _mask_token("a")
    '*'
    >>> _mask_token("ab")
    'a*'
    >>> _mask_token("john")
    'j***n'
    """
    s = (s or "").strip()
    n = len(s)
    if n <= 0:
        return ""
    if n == 1:
        return "*"
    if n == 2:  # noqa: PLR2004
        return f"{s[0]}*"
    return f"{s[0]}***{s[-1]}"


def obfuscate_email(email: str) -> str:
    """
    Lightly obfuscate an email address (mask local and part of domain).

    >>> obfuscate_email("")
    ''
    >>> obfuscate_email("notanemail")
    'n***l'
    >>> obfuscate_email("a@b.com")
    '*@*.com'
    >>> obfuscate_email("ab@xy.org")
    'a*@x*.org'
    >>> obfuscate_email("john.doe@example.com")
    'j***e@e***e.com'
    >>> obfuscate_email("user@mail.example.co.uk")
    'u***r@c*.uk'
    """
    value = (email or "").strip()
    if not value:
        return ""

    local, sep, domain = value.partition("@")
    if not sep:
        return _mask_token(value)

    masked_local = _mask_token(local)

    # Mask domain: keep TLD, mask SLD; ignore deeper subdomains for simplicity
    labels = domain.split(".")
    min_labels_for_tld = 2
    if len(labels) >= min_labels_for_tld:
        masked_domain = f"{_mask_token(labels[-2])}.{labels[-1]}"
    else:
        masked_domain = _mask_token(domain)

    return f"{masked_local}@{masked_domain}"
