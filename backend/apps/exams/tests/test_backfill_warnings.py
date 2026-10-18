"""`manage.py backfill_warnings`: só os avisos de laudos já importados mudam (PDFs sintéticos)."""

import json
from pathlib import Path

import pytest
from django.core.management.base import CommandError

from apps.exams.codes import WarningCode
from apps.exams.management.commands.import_reports import sha256_of
from apps.exams.models import DocumentStatus, ExamDocument, ExamResult, ExtractionRun, Laboratory

from .conftest import PAGE_2, build_pdf, header_with_accession, make_user, qualitative_block
from .report_set import db_counts, run_command

pytestmark = pytest.mark.django_db

LEGACY = [
    {"code": "block_unreadable", "params": {"page": 2}},
    {"code": "llm_budget_exceeded", "params": {"seconds": 0}},
]


@pytest.fixture
def folder(tmp_path) -> Path:
    pasta = tmp_path / "laudos"
    pasta.mkdir()
    # Página 1: exames lidos por regras; página 2: bloco qualitativo que as regras não leem.
    build_pdf(
        pasta / "a.pdf",
        [PAGE_2, qualitative_block("NITRITO", "Negativo")],
        identity=header_with_accession("021-00001-001"),
    )
    # Laudo sem nenhum resultado: a leitura nova falha.
    build_pdf(
        pasta / "vazio.pdf",
        [[[(40, "RESSONÂNCIA MAGNÉTICA DO JOELHO")]]],
        identity=header_with_accession("021-00003-003"),
    )
    return pasta


@pytest.fixture
def imported(user, folder):
    """O laudo `a.pdf` já importado, com os avisos no formato antigo (só a página)."""
    run_command("import_reports", str(folder / "a.pdf"), "--email", user.email, "--no-llm")
    document = ExamDocument.objects.get(user=user, original_filename="a.pdf")
    ExamDocument.objects.filter(pk=document.pk).update(warnings=LEGACY)
    document.refresh_from_db()
    return document


def backfill(folder, user, *args: str) -> str:
    return run_command("backfill_warnings", str(folder), "--email", user.email, *args)


def counts_of(output: str) -> dict[str, int]:
    """Linhas `  rótulo:   N` do resumo -> {rótulo: N}."""
    found = {}
    for line in output.splitlines():
        label, sep, number = line.strip().rpartition(":")
        if sep and number.strip().isdigit():
            found[label] = int(number)
    return found


def stored(document: ExamDocument) -> dict:
    """Tudo do laudo e dos resultados, menos os avisos do laudo."""
    document.refresh_from_db()
    fields = {
        f.name: getattr(document, f.name)
        for f in ExamDocument._meta.concrete_fields
        if f.name != "warnings"
    }
    rows = list(ExamResult.objects.filter(document=document).order_by("pk").values())
    return {"document": fields, "results": rows}


def test_replaces_only_the_warnings_of_the_matching_document(user, folder, imported):
    before = stored(imported)
    counts = db_counts()

    output = backfill(folder, user)

    imported.refresh_from_db()
    assert {"code": "block_unreadable", "params": {"page": 2, "title": "NITRITO"}} in (
        imported.warnings
    )
    assert imported.warnings != LEGACY
    assert stored(imported) == before  # resultados, status, laboratório... e updated_at
    assert db_counts() == counts
    assert counts_of(output)["avisos de bloco com título"] == 1


def test_dry_run_writes_nothing_and_reports_the_counts(user, folder, imported):
    before = stored(imported)
    counts = db_counts()

    output = backfill(folder, user, "--dry-run")

    imported.refresh_from_db()
    assert imported.warnings == LEGACY
    assert stored(imported) == before
    assert db_counts() == counts
    counts = counts_of(output)
    assert counts["laudos cujos avisos mudariam"] == 1
    assert counts["avisos de bloco com título"] == 1
    assert counts["com laudo existente (sha256)"] == 1
    assert counts["sem laudo correspondente"] == 1  # vazio.pdf não foi importado
    assert "Simulação: nada foi gravado." in output


def test_second_run_finds_nothing_to_change(user, folder, imported):
    backfill(folder, user)
    after_first = list(
        ExamDocument.objects.filter(pk=imported.pk).values_list("warnings", flat=True)
    )

    output = backfill(folder, user)

    assert list(ExamDocument.objects.filter(pk=imported.pk).values_list("warnings", flat=True)) == (
        after_first
    )
    counts = counts_of(output)
    assert counts["laudos cujos avisos mudaram"] == 0 and counts["sem mudança"] == 1


def test_failed_fresh_extraction_never_overwrites_the_stored_warnings(user, folder):
    # O laudo guardado tem o hash do PDF vazio, mas a leitura nova dele falha (sem resultados).
    digest = sha256_of(folder / "vazio.pdf")
    document = ExamDocument.objects.create(
        user=user,
        sha256=digest,
        original_filename="vazio.pdf",
        status=DocumentStatus.NEEDS_REVIEW,
        warnings=LEGACY,
    )

    output = backfill(folder, user)

    document.refresh_from_db()
    assert document.warnings == LEGACY
    assert counts_of(output)["leitura nova falhou"] == 1


def test_files_without_a_matching_document_are_ignored(user, folder):
    counts = db_counts()

    output = backfill(folder, user)

    assert db_counts() == counts
    counts = counts_of(output)
    assert counts["com laudo existente (sha256)"] == 0
    assert counts["sem laudo correspondente"] == 2


def test_another_users_document_with_the_same_hash_is_untouched(user, folder, imported):
    other = make_user("outra@example.com", "Outra", "Pessoa", "female")
    twin = ExamDocument.objects.create(
        user=other,
        sha256=imported.sha256,
        original_filename="a.pdf",
        status=DocumentStatus.NEEDS_REVIEW,
        warnings=LEGACY,
    )

    backfill(folder, user)

    twin.refresh_from_db()
    assert twin.warnings == LEGACY
    imported.refresh_from_db()
    assert imported.warnings != LEGACY


def test_document_read_with_a_model_is_skipped(user, folder, imported):
    ExtractionRun.objects.create(document=imported, status="success", stats={"llm_calls": 3})

    output = backfill(folder, user)

    imported.refresh_from_db()
    assert imported.warnings == LEGACY
    assert counts_of(output)["lidos com modelo (pulados)"] == 1


def test_document_still_being_read_is_skipped(user, folder, imported):
    ExamDocument.objects.filter(pk=imported.pk).update(status=DocumentStatus.EXTRACTING)

    backfill(folder, user)

    imported.refresh_from_db()
    assert imported.warnings == LEGACY


def test_scratch_user_and_laboratories_do_not_leak(user, folder, imported):
    labs = list(Laboratory.objects.values_list("pk", flat=True))

    backfill(folder, user)

    assert list(Laboratory.objects.values_list("pk", flat=True)) == labs
    assert not ExamDocument.objects.exclude(user=user).exists()


def test_original_pdfs_are_never_touched(user, folder, imported):
    before = {p.name: p.read_bytes() for p in folder.glob("*.pdf")}

    backfill(folder, user)

    assert {p.name: p.read_bytes() for p in folder.glob("*.pdf")} == before
    assert sorted(p.name for p in folder.iterdir()) == sorted(before)


def test_terminal_output_has_only_names_and_counts(user, folder, imported):
    output = backfill(folder, user)

    for forbidden in ("NITRITO", "Negativo", "1,20", "JOAO", "021-00001-001"):
        assert forbidden not in output


def test_no_pdf_found_is_an_error(user, tmp_path):
    (tmp_path / "vazia").mkdir()

    with pytest.raises(CommandError, match="Nenhum PDF"):
        run_command("backfill_warnings", str(tmp_path / "vazia"), "--email", user.email)


def test_warning_json_stays_serializable(user, folder, imported):
    backfill(folder, user)

    imported.refresh_from_db()
    json.dumps(imported.warnings)
    assert all(w["code"] in {str(c) for c in WarningCode} for w in imported.warnings)
