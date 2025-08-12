from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('talks', '0008_remove_question_author_email_and_more'),
    ]

    operations = [
        migrations.AlterField(
            model_name='question',
            name='status',
            field=models.CharField(choices=[('approved', 'Approved'), ('answered', 'Answered'), ('rejected', 'Rejected')], default='approved', help_text='Status of the question', max_length=10),
        ),
    ]
