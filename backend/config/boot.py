from django.conf import settings
from django.core.management import call_command


def run_pending_migrations() -> None:
    """Aplica as migrations pendentes ao subir o servidor (desenvolvimento).

    Desligado por `DJANGO_AUTO_MIGRATE=false` (padrão na Vercel): numa função serverless cada
    partida a frio migraria o banco pelo pooler. Lá, migrar é `mise run cloud-migrate`.
    """
    if not settings.AUTO_MIGRATE:
        return
    call_command("migrate", interactive=False)
