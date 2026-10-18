"""Exporta laudos antigos (PDF) para um JSON revisável, SEM gravar nada no banco.

    manage.py export_reports /caminho/pasta -o old_reports.json
    manage.py export_reports /pasta1 /pasta2/laudo.pdf -o out.json --recursive --email EU@x.com

Passo 1 da carga única em duas etapas (o passo 2 é `import_json`). Roda o MESMO pipeline do
`import_reports --no-llm` (só regras: nenhuma chamada ao modelo, o Ollama nem precisa estar de pé)
e grava o que foi extraído num JSON que você pode revisar e editar.

Garantias:
- O banco não muda: cada laudo roda numa transação sempre revertida (e o catálogo é sincronizado
  dentro da transação externa, também revertida). Roda como um usuário descartável com cópia do
  perfil (sexo, nascimento) e dos apelidos de `--email`, então o resultado não depende do que já
  está importado e nada do usuário real é tocado, nem de passagem.
- Os PDFs originais nunca são alterados nem apagados: o pipeline lê uma cópia privada temporária,
  apagada ao fim de cada laudo.
- O arquivo de saída é criado com permissão 0600 e não sobrescreve outro sem `--force`.
- A saída do terminal tem só nomes de arquivo, status e contagens; nunca valores de exame.
"""

import json
import os
import subprocess
import tempfile
import time
import uuid
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.test.utils import override_settings

from apps.exams import reports_json, tasks
from apps.exams.catalog import sync_catalog
from apps.exams.codes import ErrorCode
from apps.exams.models import (
    Analyte,
    AnalyteAlias,
    ExamDocument,
    Profile,
    ResultOrigin,
    ReviewStatus,
    UnitConversion,
)
from apps.exams.storage import PDF_MAGIC, delete_upload, save_upload

from .import_reports import chunks_of, find_pdfs, resolve_user, sha256_of


class _Rollback(Exception):
    """Desfaz a transação e leva o resultado já serializado para fora dela."""

    def __init__(self, payload=None) -> None:
        super().__init__()
        self.payload = payload


def git_ignore_warning(path: Path) -> str | None:
    """Aviso se `path` está dentro de um repositório git e NÃO é ignorado (senão, `None`)."""
    try:
        directory = str(path.parent)
        top = subprocess.run(
            ["git", "-C", directory, "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if top.returncode != 0:
            return None
        ignored = subprocess.run(
            ["git", "-C", directory, "check-ignore", "-q", str(path)],
            capture_output=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if ignored.returncode == 1:  # 0 = ignorado; 128 = erro
        return (
            f"Atenção: {path.name} está dentro de um repositório git e NÃO está ignorado. "
            "Ele contém dados de saúde: adicione-o ao .gitignore antes de qualquer commit."
        )
    return None


def write_private(path: Path, text: str) -> None:
    """Grava `path` com permissão 0600, de uma vez (arquivo temporário + rename)."""
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=".export-", suffix=".tmp")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


class Command(BaseCommand):
    help = "Exporta laudos em PDF para um JSON revisável, sem gravar nada no banco."

    def add_arguments(self, parser):
        parser.add_argument("paths", nargs="+", type=Path, help="PDFs e/ou pastas com PDFs.")
        parser.add_argument("-o", "--output", type=Path, required=True, help="JSON de saída.")
        parser.add_argument("--recursive", action="store_true", help="Entra nas subpastas.")
        parser.add_argument("--limit", type=int, default=0, help="Processa no máximo N arquivos.")
        parser.add_argument(
            "--email",
            help="Usuário cujo perfil e apelidos valem no processamento (e-mail ou usuário). "
            "Opcional se só há um usuário.",
        )
        parser.add_argument(
            "--include-text",
            action="store_true",
            help="Inclui o texto das páginas (já sem CPF/RG) em cada laudo.",
        )
        parser.add_argument("--force", action="store_true", help="Sobrescreve o JSON de saída.")

    def handle(self, *args, **options):
        template = resolve_user(options["email"])
        output: Path = options["output"].expanduser()
        self._check_output(output, force=options["force"])
        files = find_pdfs(options["paths"], options["recursive"])
        if options["limit"] > 0:
            files = files[: options["limit"]]
        if not files:
            raise CommandError("Nenhum PDF encontrado.")

        self.stdout.write(
            f"{len(files)} arquivo(s), perfil de {template.get_username()} — só regras, "
            "sem modelo; o banco não é alterado"
        )
        started = time.monotonic()
        entries = self._extract(template, files, include_text=options["include_text"])
        write_private(output, self._dump(entries))

        self._summary(entries, time.monotonic() - started, output)

    # --- saída ----------------------------------------------------------------------------

    def _check_output(self, output: Path, *, force: bool) -> None:
        if not output.parent.is_dir():
            raise CommandError(f"A pasta de saída não existe: {output.parent}")
        if output.is_dir():
            raise CommandError(f"A saída precisa ser um arquivo, não uma pasta: {output}")
        if output.exists() and not force:
            raise CommandError(f"{output} já existe. Use --force para sobrescrever.")
        if (message := git_ignore_warning(output)) is not None:
            self.stderr.write(self.style.WARNING(message))

    @staticmethod
    def _dump(entries: list[dict]) -> str:
        return json.dumps(reports_json.build_file(entries), ensure_ascii=False, indent=2) + "\n"

    # --- extração -------------------------------------------------------------------------

    def _extract(self, template, files: list[Path], *, include_text: bool) -> list[dict]:
        entries: list[dict] = []
        seen: set[str] = set()
        # Sem Redis (eventos em memória) e sem modelo (orçamento zero: o pipeline pula todo LLM).
        offline = override_settings(
            CHANNEL_LAYERS={"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}},
            EXTRACTION_LLM_BUDGET_SECONDS=0,
        )
        try:
            with offline, transaction.atomic():
                sync_catalog(Analyte, AnalyteAlias, UnitConversion)  # revertido no fim
                for index, path in enumerate(files, start=1):
                    entry = self._one(template, path, include_text=include_text)
                    if entry["sha256"] in seen:
                        self.stdout.write(
                            f"[{index}/{len(files)}] {path.name}: pulado (mesmo conteúdo de outro)"
                        )
                        continue
                    seen.add(entry["sha256"])
                    entries.append(entry)
                    self.stdout.write(f"[{index}/{len(files)}] {self._line(path.name, entry)}")
                raise _Rollback
        except _Rollback:
            pass
        except KeyboardInterrupt:
            raise CommandError("Interrompido: nada foi gravado.") from None
        return entries

    def _one(self, template, path: Path, *, include_text: bool) -> dict:
        name = path.name
        try:
            size = path.stat().st_size
            with path.open("rb") as handle:
                head = handle.read(len(PDF_MAGIC))
            digest = sha256_of(path)
        except OSError:
            return reports_json.failed_document_entry(
                source_file=name, sha256="0" * 64, error_code=ErrorCode.PDF_UNREADABLE
            )
        if head != PDF_MAGIC:
            error = ErrorCode.FILE_NOT_PDF
        elif size > settings.EXAMS_MAX_UPLOAD_BYTES:
            error = ErrorCode.FILE_TOO_LARGE
        else:
            error = None
        if error is not None:
            return reports_json.failed_document_entry(
                source_file=name, sha256=digest, error_code=error
            )

        # Cópia privada: o pipeline apaga `upload_path` ao terminar; o original fica intacto.
        stored = save_upload(chunks_of(path))
        try:
            with transaction.atomic():
                scratch = self._scratch_user(template)
                document = ExamDocument.objects.create(
                    user=scratch,
                    sha256=digest,
                    original_filename=name[:255],
                    upload_path=str(stored.path),
                )
                tasks.process_exam_document(document.pk)
                document.refresh_from_db()
                raise _Rollback(
                    reports_json.serialize_document(
                        document, source_file=name, include_text=include_text
                    )
                )
        except _Rollback as rolled_back:
            return rolled_back.payload
        finally:
            delete_upload(stored.path)  # rede de segurança se a tarefa for interrompida

    @staticmethod
    def _scratch_user(template):
        """Usuário descartável (existe só dentro da transação revertida) com o perfil e os
        apelidos do usuário escolhido: o pipeline vê o mesmo contexto, sem laudos anteriores."""
        scratch = get_user_model().objects.create(
            username=f"export-{uuid.uuid4().hex}",
            first_name=template.first_name,
            last_name=template.last_name,
        )
        profile = Profile.objects.filter(user=template).first()
        if profile is not None:
            Profile.objects.create(user=scratch, sex=profile.sex, birth_date=profile.birth_date)
        AnalyteAlias.objects.bulk_create(
            AnalyteAlias(
                analyte_id=alias.analyte_id,
                user=scratch,
                alias_normalized=alias.alias_normalized,
                source=alias.source,
            )
            for alias in AnalyteAlias.objects.filter(user=template)
        )
        return scratch

    # --- terminal (só nomes e contagens) --------------------------------------------------

    @staticmethod
    def _line(name: str, entry: dict) -> str:
        if entry["status"] == "failed":
            return f"{name}: FALHOU — {entry['error_code']}"
        current = [r for r in entry["results"] if r["origin"] == ResultOrigin.CURRENT]
        pending = sum(1 for r in current if r["review_status"] == ReviewStatus.NEEDS_REVIEW)
        previous = len(entry["results"]) - len(current)
        return (
            f"{name}: {entry['status']} — {len(current)} resultados, {previous} anteriores, "
            f"{pending} p/ revisar"
        )

    def _summary(self, entries: list[dict], elapsed: float, output: Path) -> None:
        by_status: dict[str, int] = {}
        for entry in entries:
            by_status[entry["status"]] = by_status.get(entry["status"], 0) + 1
        results = sum(len(e["results"]) for e in entries)
        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS(f"Resumo ({elapsed:.0f}s):"))
        self.stdout.write(f"  laudos no arquivo:  {len(entries)}")
        for status in sorted(by_status):
            self.stdout.write(f"  {status}: {by_status[status]}")
        self.stdout.write(f"  linhas de resultado: {results}")
        self.stdout.write(f"JSON gravado em {output} (permissão 0600; contém dados de saúde).")
        self.stdout.write("Revise e edite; depois: manage.py import_json <arquivo> --email <você>.")
