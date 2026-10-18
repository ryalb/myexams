"""Zera o banco de desenvolvimento e aplica as migrations do zero.

    manage.py reset_db --yes                 # apaga TUDO (inclusive as contas)
    manage.py reset_db --yes --keep-users    # apaga tudo, mas devolve contas e perfis

Sem `--yes`, pede para digitar o nome do banco. Só roda em banco local (localhost/127.0.0.1/::1),
sem exceção: o objetivo é reiniciar o ambiente de desenvolvimento, nunca um banco compartilhado.

`--keep-users` salva as contas (`auth.user`) e os perfis (`exams.profile`: sexo e nascimento, que
as faixas de referência usam) num arquivo temporário privado (0600), recria o esquema e os
recarrega com as mesmas senhas. Se algo falhar depois de apagar, o arquivo NÃO é removido e o
caminho é mostrado, para que as contas não se percam.

Faça um backup antes (`mise run db-backup`; a tarefa `db-reset` já depende dele).
"""

import os
import tempfile
from pathlib import Path

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import connection

LOCAL_HOSTS = frozenset({"", "localhost", "127.0.0.1", "::1"})
KEEP_MODELS = ("auth.user", "exams.profile")


def assert_local_database() -> dict:
    """Devolve a configuração do banco; recusa qualquer um que não seja da própria máquina."""
    config = settings.DATABASES["default"]
    host = str(config.get("HOST") or "").strip().lower()
    if host not in LOCAL_HOSTS:
        raise CommandError(
            f"Recusado: o banco está em '{host}'. reset_db só roda em banco local "
            "(localhost, 127.0.0.1 ou ::1)."
        )
    return config


def drop_and_recreate_schema() -> None:
    """Apaga tudo o que há no esquema `public` (tabelas, extensões, tipos) e o recria."""
    with connection.cursor() as cursor:
        cursor.execute("DROP SCHEMA public CASCADE")
        cursor.execute("CREATE SCHEMA public")


class Command(BaseCommand):
    help = "Apaga o banco local e roda as migrations do zero (opcionalmente mantendo as contas)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--yes", action="store_true", help="Não pergunta: confirma que quer apagar o banco."
        )
        parser.add_argument(
            "--keep-users",
            action="store_true",
            help="Guarda e restaura as contas e os perfis (mesmas senhas).",
        )

    def handle(self, *args, **options):
        config = assert_local_database()
        name = config["NAME"]
        self._confirm(name, assume_yes=options["yes"])

        saved: Path | None = None
        if options["keep_users"]:
            saved = self._save_users()
            self.stdout.write(f"Contas guardadas em arquivo temporário privado ({saved.name}).")

        connection.close()
        drop_and_recreate_schema()
        self.stdout.write("Esquema apagado. Aplicando migrations...")
        call_command("migrate", interactive=False, verbosity=0)
        call_command("sync_catalog", verbosity=0)

        if saved is not None:
            try:
                call_command("loaddata", str(saved), verbosity=0)
            except Exception as exc:  # o arquivo fica: é a única cópia das contas
                raise CommandError(
                    f"Banco recriado, mas as contas NÃO voltaram ({type(exc).__name__}). "
                    f"O arquivo foi mantido para recuperação: {saved}"
                ) from exc
            saved.unlink()
            self.stdout.write("Contas e perfis restaurados.")

        self.stdout.write(self.style.SUCCESS(f"Banco '{name}' recriado e migrado."))
        self.stdout.write(
            self.style.WARNING(
                "O banco está VAZIO (sem laudos). Para recarregar os PDFs de fixtures/private: "
                "mise run reload-reports   (ou, de uma vez só: mise run db-fresh)"
            )
        )

    def _confirm(self, name: str, *, assume_yes: bool) -> None:
        if assume_yes:
            return
        self.stdout.write(
            self.style.WARNING(f"Isto APAGA todo o banco '{name}' (laudos, resultados, contas).")
        )
        typed = input(f"Digite o nome do banco ({name}) para confirmar: ")
        if typed.strip() != name:
            raise CommandError("Cancelado: o nome digitado não confere.")

    def _save_users(self) -> Path:
        fd, raw_path = tempfile.mkstemp(prefix="myexams-users-", suffix=".json")
        os.close(fd)
        path = Path(raw_path)
        path.chmod(0o600)
        call_command("dumpdata", *KEEP_MODELS, indent=1, output=str(path))
        return path
