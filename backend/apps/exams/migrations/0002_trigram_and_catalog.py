from django.contrib.postgres.operations import TrigramExtension
from django.db import migrations


def seed_catalog(apps, schema_editor):
    from apps.exams.catalog import sync_catalog

    sync_catalog(
        apps.get_model("exams", "Analyte"),
        apps.get_model("exams", "AnalyteAlias"),
        apps.get_model("exams", "UnitConversion"),
    )


class Migration(migrations.Migration):
    dependencies = [("exams", "0001_initial")]

    operations = [
        # Similaridade de nomes de exame (AnalyteResolver). Vem no contrib padrão.
        TrigramExtension(),
        migrations.RunPython(seed_catalog, migrations.RunPython.noop),
    ]
