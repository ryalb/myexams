"""Carrega no banco o JSON gerado por `export_reports` (e possivelmente editado por você).

    manage.py import_json old_reports.json --email voce@email.com --dry-run
    manage.py import_json old_reports.json --email voce@email.com

Passo 2 da carga única em duas etapas. Cria `ExamDocument` (sem PDF, mas com o `sha256` do original:
o mesmo PDF não pode ser enviado de novo) e `ExamResult`, um laudo por transação. As regras de
quem manda em cada campo (texto impresso x derivados, `keep`, `reviewed`, `review_status`) estão no
cabeçalho de `apps/exams/reports_json.py` e no README.

- O arquivo é validado inteiro antes de qualquer escrita; arquivo malformado, de outra versão ou
  com slug de exame inexistente não grava nada.
- Pode rodar de novo: laudo com o mesmo `sha256` ou o mesmo número de pedido (no mesmo
  laboratório) é pulado, nunca sobrescrito.
- `--dry-run` faz tudo (inclusive sincronizar o catálogo) dentro de uma transação revertida e mostra
  as mesmas contagens, sem gravar nada.
- A saída do terminal tem só nomes de arquivo, status e contagens; nunca valores de exame.
"""

import time
from contextlib import nullcontext
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.exams import reports_json
from apps.exams.catalog import CATALOG, sync_catalog
from apps.exams.models import Analyte, AnalyteAlias, UnitConversion

from .import_reports import resolve_user


class _Rollback(Exception):
    pass


LABELS = {
    "created": "importado",
    "skipped_existing": "pulado (já existe)",
    "skipped_failed": "pulado (falhou na extração)",
    "skipped_keep": "pulado (keep=false)",
    "skipped_empty": "pulado (sem resultados)",
}


class Command(BaseCommand):
    help = "Carrega no banco o JSON gerado por export_reports (revisado por você)."

    def add_arguments(self, parser):
        parser.add_argument("file", type=Path, help="JSON gerado por export_reports.")
        parser.add_argument(
            "--email", help="Dono dos laudos (e-mail ou usuário). Opcional se só há um usuário."
        )
        parser.add_argument(
            "--dry-run", action="store_true", help="Mostra o que seria criado, sem gravar nada."
        )

    def handle(self, *args, **options):
        user = resolve_user(options["email"])
        try:
            documents = reports_json.load_file(options["file"].expanduser())
        except reports_json.ReportsFileError as exc:
            raise CommandError(str(exc)) from None
        if not documents:
            raise CommandError("O arquivo não tem nenhum laudo em 'documents'.")
        self._check_slugs(documents)

        dry_run = options["dry_run"]
        self.stdout.write(
            f"{len(documents)} laudo(s) para {user.get_username()}"
            + (" (simulação: nada será gravado)" if dry_run else "")
        )
        started = time.monotonic()
        report = self._run(user, documents, dry_run=dry_run)
        for number, outcome in enumerate(report.outcomes, start=1):
            self.stdout.write(f"[{number}/{len(report.outcomes)}] {self._line(outcome)}")
        self._summary(report, time.monotonic() - started, dry_run=dry_run)

    # --- passos ---------------------------------------------------------------------------

    def _check_slugs(self, documents) -> None:
        """Falha ANTES de qualquer escrita se um slug não existe (nem no catálogo nem no banco)."""
        known = {entry.slug for entry in CATALOG} | set(
            Analyte.objects.values_list("slug", flat=True)
        )
        missing = sorted(reports_json.slugs_in(documents) - known)
        if missing:
            shown = ", ".join(missing[:10]) + (" …" if len(missing) > 10 else "")
            raise CommandError(
                f"Exame(s) com slug inexistente no catálogo: {shown}. "
                "Corrija o campo 'analyte' (ou use null) e rode de novo; nada foi gravado."
            )

    def _run(self, user, documents, *, dry_run: bool):
        try:
            with transaction.atomic() if dry_run else nullcontext():
                # Idempotente: garante que o catálogo do banco bate com o do código.
                sync_catalog(Analyte, AnalyteAlias, UnitConversion)
                analytes = {a.slug: a for a in Analyte.objects.all()}
                report = reports_json.import_documents(user, documents, analytes)
                if dry_run:
                    raise _Rollback
        except _Rollback:
            pass
        return report

    # --- terminal (só nomes e contagens) --------------------------------------------------

    @staticmethod
    def _line(outcome) -> str:
        label = LABELS[outcome.status]
        if outcome.status == "created":
            return (
                f"{outcome.name}: {label} — {outcome.results} resultados, "
                f"{outcome.history} anteriores"
            )
        return f"{outcome.name}: {label}"

    def _summary(self, report, elapsed: float, *, dry_run: bool) -> None:
        self.stdout.write("")
        title = "Resumo da simulação" if dry_run else "Resumo"
        self.stdout.write(self.style.SUCCESS(f"{title} ({elapsed:.0f}s):"))
        created = report.count("created")
        self.stdout.write(f"  {'seriam criados' if dry_run else 'criados'}: {created}")
        self.stdout.write(f"  resultados: {report.results}")
        self.stdout.write(f"  anteriores (histórico): {report.history}")
        self.stdout.write(f"  pulados (já existem): {report.count('skipped_existing')}")
        self.stdout.write(f"  falharam na extração: {report.count('skipped_failed')}")
        self.stdout.write(f"  pulados (keep=false): {report.count('skipped_keep')}")
        self.stdout.write(f"  pulados (sem resultados): {report.count('skipped_empty')}")
        self.stdout.write(f"  linhas descartadas (keep=false): {report.rows_dropped}")
        self.stdout.write(f"  reavaliados: {report.rows_rederived}")
        self.stdout.write(f"  aceitos por você: {report.rows_accepted}")
        self.stdout.write(f"  anteriores repetidos descartados: {report.history_deduplicated}")
