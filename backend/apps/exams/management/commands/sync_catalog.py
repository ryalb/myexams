from django.core.management.base import BaseCommand

from apps.exams.catalog import sync_catalog
from apps.exams.models import Analyte, AnalyteAlias, UnitConversion


class Command(BaseCommand):
    help = "Cria/atualiza o catálogo de exames (idempotente)."

    def handle(self, *args, **options):
        total = sync_catalog(Analyte, AnalyteAlias, UnitConversion)
        self.stdout.write(f"{total} exames sincronizados.")
