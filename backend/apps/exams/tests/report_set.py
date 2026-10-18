"""Conjunto sintético de PDFs e utilitários compartilhados pelos testes de export/import em JSON.

Só dados fabricados (ver `conftest.PAGE_*`): nenhum laudo real entra nos testes.
"""

import json
from io import StringIO
from pathlib import Path

from django.core.management import call_command

from apps.exams.models import (
    Analyte,
    AnalyteAlias,
    ExamDocument,
    ExamResult,
    ExtractionRun,
    Laboratory,
)

from .conftest import PAGE_1, PAGE_2, build_pdf, header_with_accession

# Laudo sem nenhum resultado (como uma ressonância): vira `failed` / `no_results_found`.
NO_RESULTS_PAGE = [[(40, "RESSONÂNCIA MAGNÉTICA DO JOELHO")]]


def build_set(folder: Path) -> dict[str, Path]:
    """a (hemograma + exames de 1 valor, com anteriores), b (só PAGE_2) e c (sem resultados)."""
    folder.mkdir(parents=True, exist_ok=True)
    return {
        "a": build_pdf(
            folder / "a_completo.pdf",
            [PAGE_1, PAGE_2],
            identity=header_with_accession("021-00001-001"),
        ),
        "b": build_pdf(
            folder / "b_simples.pdf", [PAGE_2], identity=header_with_accession("021-00002-002")
        ),
        "c": build_pdf(
            folder / "c_imagem.pdf",
            [NO_RESULTS_PAGE],
            identity=header_with_accession("021-00003-003"),
        ),
    }


def run_command(name: str, *args: str) -> str:
    out = StringIO()
    call_command(name, *args, stdout=out, stderr=out)
    return out.getvalue()


def export(folder: Path, output: Path, *args: str) -> dict:
    run_command("export_reports", str(folder), "-o", str(output), *args)
    return json.loads(output.read_text(encoding="utf-8"))


def write_json(path: Path, data: dict) -> Path:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def db_counts() -> dict[str, int]:
    from django.contrib.auth import get_user_model

    return {
        "documents": ExamDocument.objects.count(),
        "results": ExamResult.objects.count(),
        "runs": ExtractionRun.objects.count(),
        "laboratories": Laboratory.objects.count(),
        "analytes": Analyte.objects.count(),
        "aliases": AnalyteAlias.objects.count(),
        "users": get_user_model().objects.count(),
    }


DOCUMENT_FIELDS = (
    "original_filename",
    "sha256",
    "status",
    "error_code",
    "error_params",
    "warnings",
    "accession_number",
    "ordering_physician",
    "patient_name_on_doc",
    "patient_birth_date",
    "collected_on",
    "issued_at",
    "page_count",
    "extraction_method",
    "upload_path",
)

RESULT_FIELDS = (
    "origin",
    "page",
    "position",
    "name_raw",
    "section_raw",
    "value_raw",
    "unit_raw",
    "reference_raw",
    "flag_printed",
    "comparator",
    "value_numeric",
    "value_text",
    "unit",
    "value_canonical",
    "ref_kind",
    "ref_low",
    "ref_high",
    "ref_low_inclusive",
    "ref_high_inclusive",
    "ref_bands",
    "flag",
    "interpretation",
    "collected_at",
    "method",
    "specimen",
    "performing_lab",
    "notes",
    "review_status",
    "warnings",
    "source_line",
)


def snapshot(user, *, skip_failed: bool = False) -> list[dict]:
    """O que o banco guarda de laudos e resultados do usuário (sem ids nem timestamps)."""
    documents = ExamDocument.objects.filter(user=user).order_by("original_filename")
    if skip_failed:
        documents = documents.exclude(status="failed")
    out = []
    for document in documents:
        item = {field: getattr(document, field) for field in DOCUMENT_FIELDS}
        item["laboratory"] = document.laboratory.name if document.laboratory else None
        item["results"] = [
            {
                **{field: getattr(r, field) for field in RESULT_FIELDS},
                "analyte": r.analyte.slug if r.analyte else None,
            }
            for r in document.results.order_by("page", "position")
        ]
        out.append(item)
    return out
