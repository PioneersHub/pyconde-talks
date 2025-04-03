"""Utility functions for handling email addresses."""

import hashlib


def hash_email(email: str) -> str:
    """Create a SHA-256 hash of an email address."""
    return hashlib.sha256(email.encode()).hexdigest()
