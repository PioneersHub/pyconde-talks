import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('talks', '0012_talk_talks_talk_room_id_73a62a_idx_and_more'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='Rating',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('score', models.PositiveSmallIntegerField(help_text='Rating score from 1 to 5')),
                ('comment', models.TextField(blank=True, help_text='Optional comment about the talk (visible only to admins)')),
                ('created_at', models.DateTimeField(default=django.utils.timezone.now, help_text='When this rating was created')),
                ('updated_at', models.DateTimeField(auto_now=True, help_text='When this rating was last modified')),
                ('talk', models.ForeignKey(help_text='The talk being rated', on_delete=django.db.models.deletion.CASCADE, related_name='ratings', to='talks.talk')),
                ('user', models.ForeignKey(help_text='The user who submitted the rating', on_delete=django.db.models.deletion.CASCADE, related_name='ratings', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'Rating',
                'verbose_name_plural': 'Ratings',
                'ordering': ['-created_at'],
                'indexes': [models.Index(fields=['talk', 'user'], name='talks_ratin_talk_id_45e57b_idx'), models.Index(fields=['talk', '-created_at'], name='talks_ratin_talk_id_a46eae_idx')],
                'constraints': [models.UniqueConstraint(fields=('talk', 'user'), name='unique_user_talk_rating'), models.CheckConstraint(condition=models.Q(('score__gte', 1), ('score__lte', 5)), name='rating_score_range')],
            },
        ),
    ]
