"""Avalia a extração com o Ollama real sobre fixtures (opt-in; não roda no CI).

Estrutura: `<pasta>/<nome>.pdf` + `<pasta>/<nome>.expected.json`, com
`{"results": [{"analyte": "glicose", "value": "103"}, ...]}`. Os PDFs reais de
laudo contêm dado de saúde: mantenha-os em `fixtures/private/` (ignorado pelo git)
ou use PDFs sintéticos/anonimizados. Nada é gravado: cada caso roda numa
transação revertida.
"""

import json
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.exams.extraction.llm import OllamaClient
from apps.exams.extraction.pipeline import process_document
from apps.exams.models import ExamDocument, ExamResult, ResultOrigin
from apps.exams.storage import save_upload


class _Rollback(Exception):
    pass


class Command(BaseCommand):
    help = "Mede precisão/revocação da extração com o Ollama real sobre fixtures."

    def add_arguments(self, parser):
        parser.add_argument("--fixtures", default="../fixtures/private")

    def handle(self, *args, **options):
        folder = Path(options["fixtures"])
        pdfs = sorted(folder.glob("*.pdf"))
        if not pdfs:
            raise CommandError(
                f"Nenhum PDF em {folder}. Coloque laudos (sintéticos/anonimizados) "
                "e um <nome>.expected.json ao lado de cada um."
            )
        llm = OllamaClient()
        totals = {"expected": 0, "found": 0, "correct": 0}
        for pdf in pdfs:
            expected_file = pdf.with_suffix(".expected.json")
            if not expected_file.exists():
                self.stdout.write(f"{pdf.name}: sem .expected.json, ignorado")
                continue
            expected = {
                (r["analyte"], r["value"])
                for r in json.loads(expected_file.read_text(encoding="utf-8"))["results"]
            }
            found = self._run(pdf, llm)
            correct = expected & found
            totals["expected"] += len(expected)
            totals["found"] += len(found)
            totals["correct"] += len(correct)
            self.stdout.write(
                f"{pdf.name}: esperados={len(expected)} extraídos={len(found)} "
                f"corretos={len(correct)} faltando={sorted(expected - found)}"
            )
        precision = totals["correct"] / totals["found"] if totals["found"] else 0
        recall = totals["correct"] / totals["expected"] if totals["expected"] else 0
        self.stdout.write(f"precisão={precision:.2%} revocação={recall:.2%}")

    def _run(self, pdf: Path, llm: OllamaClient) -> set[tuple[str, str]]:
        found: set[tuple[str, str]] = set()
        try:
            with transaction.atomic():
                user = get_user_model().objects.create_user(username="eval", password="x")
                stored = save_upload([pdf.read_bytes()])
                document = ExamDocument.objects.create(
                    user=user,
                    sha256=stored.sha256,
                    original_filename=pdf.name,
                    upload_path=str(stored.path),
                )
                process_document(document, llm, stored.path)
                stored.path.unlink(missing_ok=True)
                for result in ExamResult.objects.filter(
                    document=document, origin=ResultOrigin.CURRENT, analyte__isnull=False
                ):
                    found.add((result.analyte.slug, result.value_raw))
                raise _Rollback
        except _Rollback:
            pass
        return found
