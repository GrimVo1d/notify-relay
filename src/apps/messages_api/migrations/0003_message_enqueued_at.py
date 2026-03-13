from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("messages_api", "0002_message_message_queued_partial_idx"),
    ]

    operations = [
        migrations.AddField(
            model_name="message",
            name="enqueued_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
