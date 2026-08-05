"""Forms for the Q&A views."""

from typing import Any, ClassVar

from django import forms
from django.utils.translation import gettext_lazy as _

from utils import turnstile

from .models_qa import Question


class QuestionForm(forms.ModelForm[Question]):
    """
    Create form for a question, with an optional Turnstile challenge.

    The captcha field only exists when Turnstile is configured, so an environment without keys
    binds and validates exactly as it did before - no hidden field, no verification call.
    """

    class Meta:
        """Metadata for QuestionForm."""

        model = Question
        fields: ClassVar[list[str]] = ["content"]

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Add the Turnstile response field when the captcha is enabled."""
        super().__init__(*args, **kwargs)
        if turnstile.is_enabled():
            self.fields[turnstile.RESPONSE_FIELD_NAME] = forms.CharField(
                required=True,
                widget=forms.HiddenInput,
                error_messages={
                    "required": _("Please complete the anti-spam check and try again."),
                },
            )

    def clean(self) -> dict[str, Any]:
        """Verify the Turnstile token with Cloudflare."""
        cleaned = super().clean() or {}
        if not turnstile.is_enabled():
            return cleaned

        token = cleaned.get(turnstile.RESPONSE_FIELD_NAME, "")
        # An absent token already failed the required check, so only a present-but-rejected one
        # needs reporting here; otherwise the user would see two errors for one mistake.
        if token and not turnstile.verify(token):
            msg = _("The anti-spam check did not pass. Please try again.")
            raise forms.ValidationError(msg)
        return cleaned
