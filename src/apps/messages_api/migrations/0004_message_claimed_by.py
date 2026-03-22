from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("messages_api", "0003_message_enqueued_at"),
    ]

    operations = [
        migrations.AddField(
            model_name="message",
            name="claimed_by",
            field=models.CharField(blank=True, default="", max_length=64),
        ),
    ]
