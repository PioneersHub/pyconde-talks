import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('talks', '0021_alter_talk_presentation_type'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name='talk',
            name='session_chair',
            field=models.ForeignKey(blank=True, help_text='User who volunteered to chair this session', null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='chaired_talks', to=settings.AUTH_USER_MODEL),
        ),
    ]
