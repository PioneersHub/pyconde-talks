import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("events", "0006_event_qa_mode"),
        ("users", "0008_alter_customuser_preferred_language"),
    ]

    operations = [
        migrations.CreateModel(
            name="EventAccessGrant",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True, primary_key=True, serialize=False, verbose_name="ID"
                    ),
                ),
                (
                    "source",
                    models.CharField(
                        choices=[
                            ("ticket", "Ticket validated by the event API"),
                            ("open_registration", "Open registration (public event, no check)"),
                            ("discord_role", "Discord role"),
                            ("transfer", "Transferred from a merged account"),
                        ],
                        help_text="How this user got access to this event.",
                        max_length=20,
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                (
                    "event",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="access_grants",
                        to="events.event",
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="event_access_grants",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "verbose_name": "Event access grant",
                "verbose_name_plural": "Event access grants",
                "constraints": [
                    models.UniqueConstraint(
                        fields=("user", "event"), name="unique_event_access_grant_per_user"
                    )
                ],
            },
        ),
    ]
