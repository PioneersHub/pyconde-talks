"""
Migration to create Question, QuestionVote, and Answer models for the Q&A feature.
"""

import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    """Migration to create Question, QuestionVote, and Answer models for the Q&A feature."""

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('talks', '0003_alter_talk_video_start_time'),
    ]

    operations = [
        migrations.CreateModel(
            name='Question',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('content', models.TextField(help_text='The question text')),
                ('author_name', models.CharField(blank=True, help_text='Name of the person asking the question (optional)', max_length=100)),
                ('author_email', models.EmailField(blank=True, help_text='Email of the person asking the question (optional)', max_length=254)),
                ('status', models.CharField(choices=[('pending', 'Pending'), ('approved', 'Approved'), ('answered', 'Answered'), ('rejected', 'Rejected')], default='pending', help_text='Status of the question', max_length=10)),
                ('is_anonymous', models.BooleanField(default=False, help_text="Whether to display the author's name")),
                ('created_at', models.DateTimeField(default=django.utils.timezone.now, help_text='When this question was asked')),
                ('updated_at', models.DateTimeField(auto_now=True, help_text='When this question was last modified')),
                ('talk', models.ForeignKey(help_text='Talk this question is about', on_delete=django.db.models.deletion.CASCADE, related_name='questions', to='talks.talk')),
                ('user', models.ForeignKey(blank=True, help_text='User who asked the question (if logged in)', null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='questions', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'Question',
                'verbose_name_plural': 'Questions',
                'ordering': ['-created_at'],
            },
        ),
        migrations.AddIndex(
            model_name='question',
            index=models.Index(fields=['talk', 'status'], name='talks_quest_talk_id_552c58_idx'),
        ),
        migrations.AddIndex(
            model_name='question',
            index=models.Index(fields=['user'], name='talks_quest_user_id_81381e_idx'),
        ),
        migrations.CreateModel(
            name='QuestionVote',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(default=django.utils.timezone.now, help_text='When this vote was created')),
                ('question', models.ForeignKey(help_text='Question being voted on', on_delete=django.db.models.deletion.CASCADE, related_name='votes', to='talks.question')),
                ('user', models.ForeignKey(help_text='User who voted', on_delete=django.db.models.deletion.CASCADE, related_name='question_votes', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'Question Vote',
                'verbose_name_plural': 'Question Votes',
                'unique_together': {('question', 'user')},
            },
        ),
        migrations.AddIndex(
            model_name='questionvote',
            index=models.Index(fields=['question'], name='talks_quest_questio_5d98c1_idx'),
        ),
        migrations.AddIndex(
            model_name='questionvote',
            index=models.Index(fields=['user'], name='talks_quest_user_id_c0a17e_idx'),
        ),
        migrations.CreateModel(
            name='Answer',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('content', models.TextField(help_text='The answer text')),
                ('is_official', models.BooleanField(default=False, help_text='Whether this is an official answer from a speaker or organizer')),
                ('created_at', models.DateTimeField(default=django.utils.timezone.now, help_text='When this answer was created')),
                ('updated_at', models.DateTimeField(auto_now=True, help_text='When this answer was last modified')),
                ('question', models.ForeignKey(help_text='Question this answer responds to', on_delete=django.db.models.deletion.CASCADE, related_name='answers', to='talks.question')),
                ('user', models.ForeignKey(help_text='User who provided the answer', null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='answers', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'Answer',
                'verbose_name_plural': 'Answers',
                'ordering': ['created_at'],
            },
        ),
        migrations.AddIndex(
            model_name='answer',
            index=models.Index(fields=['question'], name='talks_answe_questio_77795a_idx'),
        ),
        migrations.AddIndex(
            model_name='answer',
            index=models.Index(fields=['user'], name='talks_answe_user_id_bd34d2_idx'),
        ),
    ],
)