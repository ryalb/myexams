"""Atualiza SÓ os avisos (`warnings`) de laudos já importados, com a extração atual (só regras).

    manage.py backfill_warnings /caminho/pasta --email voce@email.com --dry-run
    manage.py backfill_warnings /pasta1 /pasta2/laudo.pdf --email voce@email.com --recursive

Para que serve: o aviso `block_unreadable` ganhou o nome do exame (`title`). Laudos importados antes
só guardam a página. Este comando relê o PDF, refaz a extração sem modelo e troca os avisos do
laudo que JÁ existe (mesmo `sha256`, mesmo usuário) pelos novos.

Garantias:
- Só `ExamDocument.warnings` muda, e só quando os avisos novos diferem dos guardados. Resultados,
  status, laboratório, contadores, revisão e o resto do laudo ficam como estão.
- PDFs sem laudo correspondente (por `sha256`) são ignorados. A leitura nova falhou: os avisos
  guardados ficam como estão (nunca se sobrescreve com uma leitura que falhou).
- Laudo que foi lido com modelo (alguma execução com chamada ao LLM) é pulado: a leitura só com
  regras acusaria como "não lido" blocos que o modelo havia lido.
- Mesma maquinaria do `export_reports`: sem modelo, em transação sempre revertida, como um usuário
  descartável com o perfil e os apelidos de `--email`. O banco só muda no fim, nos avisos.
- Os PDFs originais nunca são alterados nem apagados.
- Avisos dispensados antes ("Marcar como resolvido") voltam, pois a leitura nova não sabe deles:
  rode este comando uma vez, antes de usar a dispensa.
- `--dry-run` faz a leitura e mostra as contagens, sem gravar nada. A saída tem só nomes de
  arquivo e contagens, nunca valores de exame.
"""

import time
from dataclasses import dataclass
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.exams.catalog import sync_catalog
from apps.exams.codes import WarningCode
from apps.exams.models import (
    Analyte,
    AnalyteAlias,
    DocumentStatus,
    ExamDocument,
    UnitConversion,
)
from apps.exams.offline import offline_settings

from .export_reports import Command as ExportCommand
from .export_reports import _Rollback
from .import_reports import find_pdfs, resolve_user, sha256_of

BUSY = (DocumentStatus.UPLOADED, DocumentStatus.EXTRACTING)


@dataclass
class _Item:
    path: Path
    sha256: str
    document: ExamDocument | None = None
    fresh: list[dict] | None = None  # avisos novos; None = a leitura nova falhou
    state: str = ""  # sem_laudo | ocupado | com_modelo | falhou | igual | alterado


@dataclass
class _Totals:
    files: int = 0
    matched: int = 0
    unmatched: int = 0
    busy: int = 0
    model_read: int = 0
    failed: int = 0
    unchanged: int = 0
    changed: int = 0
    titled: int = 0  # avisos `block_unreadable` com título nos laudos que mudam


def _titled(warnings: list[dict]) -> int:
    return sum(
        1
        for w in warnings
        if w.get("code") == WarningCode.BLOCK_UNREADABLE and (w.get("params") or {}).get("title")
    )


def _read_with_model(document: ExamDocument) -> bool:
    return document.runs.filter(stats__llm_calls__gt=0).exists()


class Command(BaseCommand):
    help = "Atualiza só os avisos de laudos já importados, relendo os PDFs (só regras)."

    def add_arguments(self, parser):
        parser.add_argument("paths", nargs="+", type=Path, help="PDFs e/ou pastas com PDFs.")
        parser.add_argument(
            "--email",
            help="Dono dos laudos (e-mail ou usuário). Opcional se só há um usuário.",
        )
        parser.add_argument("--recursive", action="store_true", help="Entra nas subpastas.")
        parser.add_argument("--dry-run", action="store_true", help="Só mostra as contagens.")

    def handle(self, *args, **options):
        user = resolve_user(options["email"])
        files = find_pdfs(options["paths"], options["recursive"])
        if not files:
            raise CommandError("Nenhum PDF encontrado.")
        dry_run = options["dry_run"]
        self.stdout.write(
            f"{len(files)} arquivo(s) para {user.get_username()} — só regras, sem modelo"
            + (" (simulação: nada será gravado)" if dry_run else "")
        )

        started = time.monotonic()
        items = self._match(user, files)
        self._read_fresh(user, items)
        totals = self._decide(items)
        if not dry_run:
            self._write(user, items)
        for number, item in enumerate(items, start=1):
            self.stdout.write(f"[{number}/{len(items)}] {self._line(item)}")
        self._summary(totals, time.monotonic() - started, dry_run=dry_run)

    # --- passos ---------------------------------------------------------------------------

    @staticmethod
    def _match(user, files: list[Path]) -> list[_Item]:
        """Cada PDF com o laudo existente do usuário que tem o mesmo conteúdo (sha256)."""
        items = [_Item(path, sha256_of(path)) for path in files]
        existing = {
            d.sha256: d
            for d in ExamDocument.objects.filter(user=user, sha256__in={i.sha256 for i in items})
        }
        seen: set[str] = set()
        for item in items:
            document = existing.get(item.sha256)
            if document is None:
                item.state = "sem_laudo"
            elif item.sha256 in seen:
                item.state = "repetido"  # mesmo conteúdo de outro arquivo desta execução
            elif document.status in BUSY:
                item.state = "ocupado"
            elif _read_with_model(document):
                item.state = "com_modelo"
            else:
                item.document = document
            seen.add(item.sha256)
        return items

    def _read_fresh(self, user, items: list[_Item]) -> None:
        """Extrai de novo (só regras), numa transação sempre revertida. Nada fica no banco."""
        todo = [i for i in items if i.document is not None]
        if not todo:
            return
        exporter = ExportCommand()
        try:
            with offline_settings(), transaction.atomic():
                sync_catalog(Analyte, AnalyteAlias, UnitConversion)  # revertido no fim
                for item in todo:
                    item.fresh = self._extract_warnings(exporter, user, item.path)
                raise _Rollback
        except _Rollback:
            pass
        except KeyboardInterrupt:
            raise CommandError("Interrompido: nada foi gravado.") from None

    @staticmethod
    def _extract_warnings(exporter: ExportCommand, user, path: Path) -> list[dict] | None:
        try:
            entry = exporter._one(user, path, include_text=False)
        except Exception:
            return None  # a leitura nova falhou: os avisos guardados ficam como estão
        if entry["status"] == DocumentStatus.FAILED:
            return None
        return entry["warnings"]

    @staticmethod
    def _decide(items: list[_Item]) -> _Totals:
        totals = _Totals(files=len(items))
        for item in items:
            if item.state == "sem_laudo":
                totals.unmatched += 1
                continue
            if item.state == "repetido":
                continue
            totals.matched += 1
            if item.state == "ocupado":
                totals.busy += 1
            elif item.state == "com_modelo":
                totals.model_read += 1
            elif item.document is not None:
                if item.fresh is None:
                    item.state = "falhou"
                    totals.failed += 1
                elif item.fresh == item.document.warnings:
                    item.state = "igual"
                    totals.unchanged += 1
                else:
                    item.state = "alterado"
                    totals.changed += 1
                    totals.titled += _titled(item.fresh)
        return totals

    @staticmethod
    def _write(user, items: list[_Item]) -> None:
        """Troca só `warnings`. `update` não mexe nos demais campos (nem em `updated_at`)."""
        for item in items:
            if item.state == "alterado" and item.document is not None and item.fresh is not None:
                ExamDocument.objects.filter(pk=item.document.pk, user=user).update(
                    warnings=item.fresh
                )

    # --- terminal (só nomes e contagens) --------------------------------------------------

    @staticmethod
    def _line(item: _Item) -> str:
        labels = {
            "sem_laudo": "sem laudo correspondente",
            "repetido": "mesmo conteúdo de outro arquivo (pulado)",
            "ocupado": "laudo em leitura (pulado)",
            "com_modelo": "lido com modelo (pulado)",
            "falhou": "leitura nova falhou (avisos mantidos)",
            "igual": "sem mudança",
        }
        if item.state == "alterado" and item.fresh is not None:
            return (
                f"{item.path.name}: avisos atualizados ({len(item.fresh)} aviso(s), "
                f"{_titled(item.fresh)} com título)"
            )
        return f"{item.path.name}: {labels.get(item.state, item.state)}"

    def _summary(self, totals: _Totals, elapsed: float, *, dry_run: bool) -> None:
        rows = [
            ("arquivos", totals.files),
            ("com laudo existente (sha256)", totals.matched),
            ("sem laudo correspondente", totals.unmatched),
            (
                "laudos cujos avisos mudariam" if dry_run else "laudos cujos avisos mudaram",
                totals.changed,
            ),
            ("avisos de bloco com título", totals.titled),
            ("sem mudança", totals.unchanged),
            ("leitura nova falhou", totals.failed),
            ("lidos com modelo (pulados)", totals.model_read),
            ("em leitura (pulados)", totals.busy),
        ]
        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS(f"Resumo ({elapsed:.0f}s):"))
        for label, count in rows:
            self.stdout.write(f"  {label + ':':<36}{count}")
        if dry_run:
            self.stdout.write("Simulação: nada foi gravado.")
