from django.core.management.base import BaseCommand

from apps.exams.tasks import sweep_orphans


class Command(BaseCommand):
    help = "Apaga PDFs temporários órfãos e marca como falhos os laudos presos em extração."

    def add_arguments(self, parser):
        parser.add_argument(
            "--hours",
            type=float,
            default=None,
            help="Idade mínima em horas (padrão: EXAMS_ORPHAN_HOURS, 3).",
        )

    def handle(self, *args, **options):
        counts = sweep_orphans(options["hours"])
        self.stdout.write(
            f"{counts['documents_failed']} laudos presos marcados como falhos, "
            f"{counts['files_deleted']} arquivos apagados, "
            f"{counts['paths_cleared']} caminhos limpos."
        )
