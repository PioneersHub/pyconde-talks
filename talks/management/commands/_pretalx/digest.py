"""
Email digest sent at the end of a ``--detect-only`` import run.

Summarizes the ``PendingPretalxChange`` rows produced (or refreshed) during the
current run so admins know to open the admin and triage. Uses Django's
``send_mail`` so it works with whatever email backend the project is configured
to use (``django-anymail`` for Mailgun in production, the console backend in
dev).
"""

from typing import TYPE_CHECKING

from django.conf import settings
from django.core.mail import send_mail
from django.urls import NoReverseMatch, reverse


if TYPE_CHECKING:
    from talks.management.commands._pretalx.context import ImportContext
    from talks.models import PendingPretalxChange


#: Subject line shown to the recipient. Kept English-only to play nicely with
#: mail-client thread grouping; localized strings differ per recipient locale.
_SUBJECT_TEMPLATE = "[PyConDE/PyData] {n} Pretalx change(s) detected for {event}"


def maybe_send_digest(
    detected_changes: list[PendingPretalxChange],
    ctx: ImportContext,
) -> bool:
    """
    Send a summary email when *detected_changes* is non-empty.

    Returns ``True`` if an email was actually dispatched. No-ops when the list
    is empty (no point spamming admins on a clean run) or when the project has
    no recipients configured.
    """
    if not detected_changes:
        return False

    recipients = _resolve_recipients()
    if not recipients:
        return False

    event_label = ctx.event_obj.slug if ctx.event_obj is not None else "(no event)"
    subject = _SUBJECT_TEMPLATE.format(n=len(detected_changes), event=event_label)
    body = _build_body(detected_changes)

    send_mail(
        subject=subject,
        message=body,
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=recipients,
        fail_silently=False,
    )
    return True


def _resolve_recipients() -> list[str]:
    """
    Pick the recipient list from settings.

    ``PRETALX_DIGEST_RECIPIENTS`` wins when present (an empty list explicitly means
    "do not email anyone"). Falling back to Django's ``ADMINS`` only kicks in when
    the setting is absent or set to ``None``.
    """
    configured = getattr(settings, "PRETALX_DIGEST_RECIPIENTS", None)
    if configured is not None:
        return list(configured)
    admins = getattr(settings, "ADMINS", [])
    return [addr for _, addr in admins]


def _build_body(detected_changes: list[PendingPretalxChange]) -> str:
    """Render a plain-text body listing each pending row's summary plus an admin link."""
    lines = [
        "Hi,",
        "",
        f"{len(detected_changes)} pending Pretalx change(s) need review:",
        "",
    ]
    lines.extend(f"  - {change.summarize()}" for change in detected_changes)

    admin_url = _safe_admin_url()
    if admin_url:
        lines.extend(["", f"Review them at: {admin_url}"])

    lines.extend(["", "- The Pretalx importer"])
    return "\n".join(lines)


def _safe_admin_url() -> str:
    """Return the absolute admin URL or ``""`` when SITE_URL/reverse fails."""
    try:
        path = reverse("admin:talks_pendingpretalxchange_changelist")
    except NoReverseMatch:
        return ""
    base = getattr(settings, "SITE_URL", "")
    return f"{base.rstrip('/')}{path}" if base else path
