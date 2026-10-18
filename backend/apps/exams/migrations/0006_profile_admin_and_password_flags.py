from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("exams", "0005_result_origin_manual"),
    ]

    operations = [
        migrations.AddField(
            model_name="profile",
            name="is_admin",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="profile",
            name="must_change_password",
            field=models.BooleanField(default=False),
        ),
    ]
