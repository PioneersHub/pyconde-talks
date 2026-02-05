from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('talks', '0010_alter_talk_image_alter_talk_presentation_type'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AlterUniqueTogether(
            name='questionvote',
            unique_together=set(),
        ),
        migrations.AddConstraint(
            model_name='questionvote',
            constraint=models.UniqueConstraint(fields=('question', 'user'), name='unique_question_vote'),
        ),
    ]
