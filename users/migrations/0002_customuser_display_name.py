from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('users', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='customuser',
            name='display_name',
            field=models.CharField(blank=True, help_text="Public name shown when asking questions (optional). If blank, we'll use your full name or email.", max_length=100),
        ),
    ]
