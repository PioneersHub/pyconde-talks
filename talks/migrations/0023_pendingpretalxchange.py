import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("events", "0004_event_transcriptions_url"),
        ("talks", "0022_talk_end_time_generated"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="PendingPretalxChange",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True, primary_key=True, serialize=False, verbose_name="ID"
                    ),
                ),
                (
                    "pretalx_code",
                    models.CharField(
                        help_text="Pretalx submission code (e.g. 'ABC123')", max_length=32
                    ),
                ),
                (
                    "kind",
                    models.CharField(
                        choices=[("create", "Create"), ("update", "Update"), ("delete", "Delete")],
                        help_text="Whether this change would create, update, or delete the talk",
                        max_length=10,
                    ),
                ),
                (
                    "field_diffs",
                    models.JSONField(
                        blank=True,
                        default=dict,
                        help_text="Per-field diff: {field_name: {old: <value>, new: <value>}}. Empty for CREATE/DELETE.",
                    ),
                ),
                (
                    "speaker_diffs",
                    models.JSONField(
                        blank=True,
                        default=dict,
                        help_text="Speaker M2M diff: {added: [{code, name}], removed: [...]}.",
                    ),
                ),
                (
                    "pretalx_payload",
                    models.JSONField(
                        blank=True,
                        default=dict,
                        help_text="Full snapshot of the new values to apply. Self-contained so applying does not require re-fetching Pretalx.",
                    ),
                ),
                (
                    "first_detected_at",
                    models.DateTimeField(
                        default=django.utils.timezone.now,
                        help_text="When this diff was first observed",
                    ),
                ),
                (
                    "last_detected_at",
                    models.DateTimeField(
                        auto_now=True, help_text="Most recent detect run that confirmed this diff"
                    ),
                ),
                (
                    "applied_at",
                    models.DateTimeField(
                        blank=True,
                        help_text="When the change was applied to the local Talk (null = still pending)",
                        null=True,
                    ),
                ),
                (
                    "dismissed_at",
                    models.DateTimeField(
                        blank=True,
                        help_text="When the change was dismissed (null = still pending)",
                        null=True,
                    ),
                ),
                (
                    "applied_by",
                    models.ForeignKey(
                        blank=True,
                        help_text="Admin who applied this change",
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="applied_pretalx_changes",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "dismissed_by",
                    models.ForeignKey(
                        blank=True,
                        help_text="Admin who dismissed this change",
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="dismissed_pretalx_changes",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "event",
                    models.ForeignKey(
                        help_text="Event this submission belongs to",
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="pending_pretalx_changes",
                        to="events.event",
                    ),
                ),
                (
                    "talk",
                    models.ForeignKey(
                        blank=True,
                        help_text="Local talk this change targets (null for CREATE)",
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="pending_pretalx_changes",
                        to="talks.talk",
                    ),
                ),
            ],
            options={
                "verbose_name": "Pending Pretalx change",
                "verbose_name_plural": "Pending Pretalx changes",
                "ordering": ["-last_detected_at"],
                "indexes": [
                    models.Index(
                        fields=["event", "applied_at", "dismissed_at"],
                        name="talks_pendi_event_i_a155c5_idx",
                    ),
                    models.Index(fields=["pretalx_code"], name="talks_pendi_pretalx_bd63fe_idx"),
                    models.Index(
                        fields=["-last_detected_at"], name="talks_pendi_last_de_53fb69_idx"
                    ),
                ],
                "constraints": [
                    models.UniqueConstraint(
                        condition=models.Q(
                            ("applied_at__isnull", True), ("dismissed_at__isnull", True)
                        ),
                        fields=("event", "pretalx_code"),
                        name="unique_open_pending_change_per_submission",
                    )
                ],
            },
        ),
    ]
