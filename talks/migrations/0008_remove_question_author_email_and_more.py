from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('talks', '0007_remove_is_anonymous'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='question',
            name='author_email',
        ),
        migrations.RemoveField(
            model_name='question',
            name='author_name',
        ),
    ]
