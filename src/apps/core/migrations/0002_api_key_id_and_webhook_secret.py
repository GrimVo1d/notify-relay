from django.db import migrations, models
from django.utils import timezone


def revoke_legacy_keys(apps, schema_editor):
    # Keys from the old format all shared one prefix and were hashed with
    # argon2; they can't be verified any more, so revoke them and give each
    # a unique placeholder id before the column becomes unique.
    ApiKey = apps.get_model("core", "ApiKey")
    for key in ApiKey.objects.all():
        key.key_id = f"legacy_{key.pk}"
        key.is_active = False
        key.revoked_at = key.revoked_at or timezone.now()
        key.save(update_fields=["key_id", "is_active", "revoked_at"])


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0001_initial"),
    ]

    operations = [
        migrations.RenameField(model_name="apikey", old_name="prefix", new_name="key_id"),
        migrations.RunPython(revoke_legacy_keys, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="apikey",
            name="key_id",
            field=models.CharField(max_length=32, unique=True),
        ),
        migrations.AddField(
            model_name="apikey",
            name="webhook_secret",
            field=models.CharField(blank=True, default="", max_length=64),
        ),
    ]
