"""Importa laudos em PDF em lote, offline (sem Celery, Redis nem navegador).

    manage.py import_reports /caminho/pasta --email voce@email.com
    manage.py import_reports /pasta1 /pasta2/laudo.pdf --email voce@email.com --recursive
    manage.py import_reports /pasta --email voce@email.com --no-llm --report resumo.csv

Usa o MESMO pipeline do app (regras primeiro; o modelo local só entra nos blocos que as regras
não reconhecem, com teto de tempo), de forma síncrona e um arquivo por vez.

Garantias:
- Os PDFs originais NUNCA são alterados nem apagados: cada um é copiado para o diretório privado
  temporário (o mesmo do upload pela web) e só a cópia é apagada ao fim da leitura.
- Pode rodar de novo à vontade: arquivos já importados (mesmo conteúdo) e laudos de um pedido que
  já existe são pulados. Ctrl+C encerra com limpeza depois do arquivo atual.
- Nada de valores de exame na saída: só nome do arquivo, status e contagens.
"""

import csv
import fnmatch
import hashlib
import json
import time
from contextlib import nullcontext
from dataclasses import dataclass
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from apps.exams import tasks
from apps.exams.codes import ErrorCode
from apps.exams.extraction.pipeline import find_accession_clash, laboratory_for
from apps.exams.models import DocumentStatus, ExamDocument, ReviewStatus
from apps.exams.offline import offline_settings
from apps.exams.storage import PDF_MAGIC, delete_upload, save_upload

CHUNK = 1024 * 1024


@dataclass
class Outcome:
    name: str
    status: str  # status do laudo, ou: skipped_duplicate | skipped_invalid | dry_run
    error_code: str = ""
    results: int = 0
    needs_review: int = 0
    seconds: float = 0.0
    llm_calls: int = 0


def find_pdfs(paths: list[Path], recursive: bool) -> list[Path]:
    found: dict[Path, None] = {}
    for path in paths:
        path = path.expanduser()
        if path.is_file():
            found[path.resolve()] = None
        elif path.is_dir():
            pattern = "**/*" if recursive else "*"
            for candidate in path.glob(pattern):
                if candidate.is_file() and candidate.suffix.lower() == ".pdf":
                    found[candidate.resolve()] = None
        else:
            raise CommandError(f"Caminho não encontrado: {path}")
    return sorted(found, key=lambda p: (str(p.parent).lower(), p.name.lower()))


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(CHUNK), b""):
            digest.update(block)
    return digest.hexdigest()


def chunks_of(path: Path):
    with path.open("rb") as handle:
        yield from iter(lambda: handle.read(CHUNK), b"")


def load_laboratory_map(path: Path) -> list[tuple[str, str]]:
    """Lê `{"padrão do nome do arquivo": "Nome do laboratório"}` (JSON), na ordem do arquivo.

    Serve para o que o texto do PDF não diz (o nome só está no logotipo). Os padrões são `fnmatch`
    sem diferenciar maiúsculas, aplicados ao NOME do arquivo.
    """
    try:
        data = json.loads(path.expanduser().read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise CommandError(f"Não consegui ler o mapa de laboratórios {path}: {exc}") from exc
    if not isinstance(data, dict) or not data:
        raise CommandError("O mapa de laboratórios deve ser um objeto JSON {padrão: nome}.")
    pairs = []
    for pattern, name in data.items():
        if not isinstance(pattern, str) or not isinstance(name, str) or not name.strip():
            raise CommandError(f"Entrada inválida no mapa de laboratórios: {pattern!r}.")
        pairs.append((pattern.lower(), " ".join(name.split())[:255]))
    return pairs


def resolve_user(email: str | None):
    """Dono dos laudos: por e-mail/usuário, ou o único usuário ativo. Compartilhado com
    `export_reports` e `import_json`."""
    users = get_user_model().objects.filter(is_active=True)
    if email:
        matches = users.filter(email__iexact=email) | users.filter(username__iexact=email)
        user = matches.first()
        if user is None:
            raise CommandError(f"Usuário não encontrado: {email}")
        return user
    if users.count() == 1:
        return users.get()
    raise CommandError("Informe --email: há mais de um usuário (ou nenhum).")


class Command(BaseCommand):
    help = "Importa laudos em PDF em lote, offline, com o mesmo pipeline do app."

    def add_arguments(self, parser):
        parser.add_argument("paths", nargs="+", type=Path, help="PDFs e/ou pastas com PDFs.")
        parser.add_argument(
            "--email", help="Dono dos laudos (e-mail ou usuário). Opcional se só há um usuário."
        )
        parser.add_argument("--recursive", action="store_true", help="Entra nas subpastas.")
        parser.add_argument("--limit", type=int, default=0, help="Processa no máximo N arquivos.")
        parser.add_argument(
            "--no-llm",
            action="store_true",
            help="Só regras: nenhuma chamada ao modelo (blocos que precisariam dele ficam "
            "marcados para revisão). Não precisa do Ollama.",
        )
        parser.add_argument("--dry-run", action="store_true", help="Só lista o que seria feito.")
        parser.add_argument("--report", type=Path, help="Grava um resumo em CSV.")
        parser.add_argument(
            "--laboratory-map",
            type=Path,
            help="JSON {padrão do nome do arquivo: nome do laboratório}. Só preenche o laudo "
            "cujo laboratório o texto do PDF não revelou (nunca troca um nome já detectado).",
        )

    # --- execução -------------------------------------------------------------------------

    def handle(self, *args, **options):
        user = self._user(options["email"])
        self.laboratory_map = (
            load_laboratory_map(options["laboratory_map"]) if options["laboratory_map"] else []
        )
        self.laboratories_filled = 0
        files = find_pdfs(options["paths"], options["recursive"])
        if options["limit"] > 0:
            files = files[: options["limit"]]
        if not files:
            raise CommandError("Nenhum PDF encontrado.")

        # Eventos em tempo real (WebSocket) não fazem sentido aqui e exigiriam o Redis ligado;
        # `--no-llm` desliga o modelo só durante este comando (override, nada vaza).
        offline = (
            nullcontext() if options["dry_run"] else offline_settings(use_llm=not options["no_llm"])
        )
        rules_only = options["no_llm"] or not settings.EXTRACTION_USE_LLM

        self.stdout.write(
            f"{len(files)} arquivo(s) para {user.get_username()}"
            + (" (simulação)" if options["dry_run"] else "")
            + (" — só regras, sem modelo" if rules_only else "")
        )

        outcomes: list[Outcome] = []
        started = time.monotonic()
        try:
            with offline:
                for index, path in enumerate(files, start=1):
                    outcome = self._one(user, path, dry_run=options["dry_run"])
                    outcomes.append(outcome)
                    self.stdout.write(f"[{index}/{len(files)}] {self._line(outcome)}")
        except KeyboardInterrupt:
            self.stdout.write(
                self.style.WARNING("\nInterrompido. Rode de novo: o que já foi importado é pulado.")
            )

        self._summary(outcomes, time.monotonic() - started)
        if options["report"]:
            self._write_report(options["report"], outcomes)

    def _user(self, email: str | None):
        return resolve_user(email)

    def _one(self, user, path: Path, *, dry_run: bool) -> Outcome:
        name = path.name
        try:
            size = path.stat().st_size
            with path.open("rb") as handle:
                head = handle.read(len(PDF_MAGIC))
        except OSError:
            return Outcome(name, "skipped_invalid", ErrorCode.PDF_UNREADABLE)
        if head != PDF_MAGIC:
            return Outcome(name, "skipped_invalid", ErrorCode.FILE_NOT_PDF)
        if size > settings.EXAMS_MAX_UPLOAD_BYTES:
            return Outcome(name, "skipped_invalid", ErrorCode.FILE_TOO_LARGE)

        digest = sha256_of(path)
        if ExamDocument.objects.filter(user=user, sha256=digest).exists():
            return Outcome(name, "skipped_duplicate", ErrorCode.DUPLICATE_DOCUMENT)
        if dry_run:
            return Outcome(name, "dry_run")

        # Cópia privada: a tarefa apaga `upload_path` ao terminar; o original fica intacto.
        stored = save_upload(chunks_of(path))
        document = ExamDocument.objects.create(
            user=user,
            sha256=stored.sha256,
            original_filename=name[:255],
            upload_path=str(stored.path),
        )
        started = time.monotonic()
        try:
            tasks.process_exam_document(document.pk)
        finally:
            delete_upload(stored.path)  # rede de segurança se a tarefa for interrompida
        elapsed = time.monotonic() - started

        document.refresh_from_db()
        if document.status == DocumentStatus.FAILED and (
            document.error_code == ErrorCode.DUPLICATE_DOCUMENT
        ):
            document.delete()  # o mesmo pedido já está no banco: não deixa um laudo falho na lista
            return Outcome(name, "skipped_duplicate", ErrorCode.DUPLICATE_DOCUMENT, seconds=elapsed)

        self._apply_laboratory_map(document, name)

        run = document.runs.first()
        stats = (run.stats if run else {}) or {}
        return Outcome(
            name,
            document.status,
            document.error_code,
            results=document.results.exclude(origin="lab_previous").count(),
            needs_review=document.results.filter(review_status=ReviewStatus.NEEDS_REVIEW).count(),
            seconds=elapsed,
            llm_calls=int(stats.get("llm_calls", 0) or 0),
        )

    def _apply_laboratory_map(self, document: ExamDocument, filename: str) -> None:
        """Dá o laboratório do mapa ao laudo que ficou sem nome (sem trocar o que foi detectado)."""
        if document.laboratory_id is not None or document.status == DocumentStatus.FAILED:
            return
        lowered = filename.lower()
        name = next(
            (n for pattern, n in self.laboratory_map if fnmatch.fnmatch(lowered, pattern)), ""
        )
        if not name:
            return
        laboratory = laboratory_for(name)
        if find_accession_clash(document.user, laboratory, document.accession_number, document):
            return
        document.laboratory = laboratory
        document.save(update_fields=["laboratory", "updated_at"])
        self.laboratories_filled += 1

    # --- saída ----------------------------------------------------------------------------

    LABELS = {
        DocumentStatus.COMPLETED: "concluído",
        DocumentStatus.NEEDS_REVIEW: "revisão necessária",
        DocumentStatus.FAILED: "FALHOU",
        "skipped_duplicate": "pulado (já importado)",
        "skipped_invalid": "pulado (arquivo inválido)",
        "dry_run": "seria importado",
    }

    def _line(self, o: Outcome) -> str:
        label = self.LABELS.get(o.status, o.status)
        detail = ""
        if o.status in (DocumentStatus.COMPLETED, DocumentStatus.NEEDS_REVIEW):
            detail = (
                f" — {o.results} resultados, {o.needs_review} p/ revisar, "
                f"{o.llm_calls} chamadas ao modelo, {o.seconds:.1f}s"
            )
        elif o.error_code:
            detail = f" — {o.error_code}"
        return f"{o.name}: {label}{detail}"

    def _summary(self, outcomes: list[Outcome], elapsed: float) -> None:
        def count(*statuses):
            return sum(1 for o in outcomes if o.status in statuses)

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS(f"Resumo ({elapsed:.0f}s):"))
        self.stdout.write(f"  concluídos:           {count(DocumentStatus.COMPLETED)}")
        self.stdout.write(f"  revisão necessária:   {count(DocumentStatus.NEEDS_REVIEW)}")
        self.stdout.write(f"  falharam:             {count(DocumentStatus.FAILED)}")
        self.stdout.write(f"  pulados (duplicados): {count('skipped_duplicate')}")
        self.stdout.write(f"  pulados (inválidos):  {count('skipped_invalid')}")
        if count("dry_run"):
            self.stdout.write(f"  seriam importados:    {count('dry_run')}")
        if self.laboratory_map:
            self.stdout.write(f"  laboratório pelo mapa: {self.laboratories_filled}")
        failed = [o for o in outcomes if o.status == DocumentStatus.FAILED]
        if failed:
            self.stdout.write(
                self.style.WARNING(
                    "Os que falharam ficam na lista de laudos com o motivo; abra e use "
                    "Reprocessar (ou rode de novo sem --no-llm)."
                )
            )
        if count(DocumentStatus.NEEDS_REVIEW):
            self.stdout.write("Abra o app em Laudos para revisar os marcados.")

    def _write_report(self, path: Path, outcomes: list[Outcome]) -> None:
        path = path.expanduser()
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(
                [
                    "arquivo",
                    "status",
                    "erro",
                    "resultados",
                    "para_revisar",
                    "segundos",
                    "chamadas_modelo",
                ]
            )
            for o in outcomes:
                writer.writerow(
                    [
                        o.name,
                        o.status,
                        o.error_code,
                        o.results,
                        o.needs_review,
                        f"{o.seconds:.1f}",
                        o.llm_calls,
                    ]
                )
        self.stdout.write(f"Resumo gravado em {path}")
