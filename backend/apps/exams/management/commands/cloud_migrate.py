"""Migra o banco da nuvem. Só roda via `mise run cloud-migrate -- --yes` (EXAMS_CLOUD_MIGRATE=1).

Usa `DATABASE_MIGRATE_URL` (a connection string DIRETA do Neon, sem `-pooler`), nunca o pooler
de transação do runtime. Imprime só host, porta, banco e esquema (nunca a senha). Passos:
`migrate` e `createcachetable` (com `DJANGO_CACHE=db`).
"""

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError


def is_database_cache() -> bool:
    return settings.CACHES.get("default", {}).get("BACKEND", "").endswith("DatabaseCache")


class Command(BaseCommand):
    help = (
        "Migra o banco apontado por DATABASE_MIGRATE_URL (e cria a tabela de cache). Exige --yes."
    )

    def add_arguments(self, parser):
        parser.add_argument("--yes", action="store_true", help="Confirma que o alvo está certo.")

    def handle(self, *args, **options):
        if not settings.CLOUD_MIGRATE_MODE:
            raise CommandError(
                "Rode por `mise run cloud-migrate -- --yes` (define EXAMS_CLOUD_MIGRATE=1)."
            )
        config = settings.DATABASES["default"]
        self.stdout.write(
            f"Alvo: host={config['HOST'] or '(soquete)'} porta={config['PORT']} "
            f"banco={config['NAME']} esquema={settings.DATABASE_SCHEMA or 'public'}"
        )
        if not options["yes"]:
            raise CommandError("Confira o alvo acima e rode de novo com --yes.")

        call_command("migrate", interactive=False)
        if is_database_cache():
            call_command("createcachetable")
        self.stdout.write(self.style.SUCCESS("Banco da nuvem migrado."))
