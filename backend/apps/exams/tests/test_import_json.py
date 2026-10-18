"""`manage.py import_json`: JSON revisado -> banco (dados sintéticos; round trip com o pipeline)."""

import copy
import json
from decimal import Decimal
from pathlib import Path

import pytest
from django.core.management.base import CommandError

from apps.exams import tasks
from apps.exams.extraction.fakes import FakeLLMClient
from apps.exams.models import (
    Analyte,
    DocumentStatus,
    ExamDocument,
    ExamResult,
    Laboratory,
    ReviewStatus,
)

from .conftest import SAMPLE_HEADER, sample_llm_results
from .report_set import build_set, db_counts, export, run_command, snapshot, write_json

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def fake_llm(monkeypatch):
    llm = FakeLLMClient(header=SAMPLE_HEADER, results=sample_llm_results)
    monkeypatch.setattr(tasks, "build_llm_client", lambda: llm)
    return llm


@pytest.fixture
def exported(user, tmp_path) -> dict:
    """Export dos 3 PDFs sintéticos (a: completo, b: simples, c: sem resultados)."""
    build_set(tmp_path / "laudos")
    return export(tmp_path / "laudos", tmp_path / "old_reports.json", "--email", user.email)


def load(tmp_path: Path, data: dict) -> Path:
    return write_json(tmp_path / "edited.json", data)


def do_import(path: Path, user, *args: str) -> str:
    return run_command("import_json", str(path), "--email", user.email, *args)


def doc_of(data: dict, name: str) -> dict:
    return next(d for d in data["documents"] if d["source_file"] == name)


def row_of(document: dict, slug: str, origin: str = "current") -> dict:
    return next(r for r in document["results"] if r["analyte"] == slug and r["origin"] == origin)


# --- ida e volta ---------------------------------------------------------------------------


def test_round_trip_matches_what_the_pipeline_stores(user, tmp_path, exported):
    # verdade: o mesmo pipeline sem modelo, gravando direto
    run_command("import_reports", str(tmp_path / "laudos"), "--email", user.email, "--no-llm")
    truth = snapshot(user, skip_failed=True)
    assert len(truth) == 2 and sum(len(d["results"]) for d in truth) > 10
    assert any(r["origin"] == "lab_previous" for d in truth for r in d["results"])
    ExamDocument.objects.all().delete()

    output = do_import(load(tmp_path, exported), user)

    assert snapshot(user) == truth
    assert "criados: 2" in output and "falharam na extração: 1" in output
    assert not ExamDocument.objects.filter(original_filename="c_imagem.pdf").exists()


def test_imported_documents_have_no_pdf_and_keep_the_original_hash(user, tmp_path, exported):
    do_import(load(tmp_path, exported), user)

    for document in ExamDocument.objects.filter(user=user):
        assert document.upload_path == ""
        assert document.sha256 == doc_of(exported, document.original_filename)["sha256"]
        assert document.runs.count() == 0
    assert ExamResult.objects.filter(user=user, origin="lab_previous").exists()


def test_same_pdf_cannot_be_uploaded_again_after_import(user, tmp_path, exported):
    from django.core.files.uploadedfile import SimpleUploadedFile
    from rest_framework.test import APIClient

    do_import(load(tmp_path, exported), user)
    client = APIClient()
    client.force_authenticate(user)
    content = (tmp_path / "laudos" / "b_simples.pdf").read_bytes()

    response = client.post(
        "/api/v1/exam-documents/",
        {"file": SimpleUploadedFile("novo.pdf", content, content_type="application/pdf")},
        format="multipart",
    )

    assert response.status_code == 409
    assert response.json()["code"] == "duplicate_document"
    assert ExamDocument.objects.filter(user=user, original_filename="novo.pdf").count() == 0


def test_import_is_idempotent(user, tmp_path, exported):
    path = load(tmp_path, exported)
    do_import(path, user)
    before = snapshot(user)

    output = do_import(path, user)

    assert snapshot(user) == before
    assert "criados: 0" in output and "pulados (já existem): 2" in output


def test_stdout_has_only_names_and_counts_never_values(user, tmp_path, exported):
    output = do_import(load(tmp_path, exported), user)
    for forbidden in ("GLICOSE", "Glicose", "1,20", "103", "JOAO", "021-00001-001"):
        assert forbidden not in output
    assert "a_completo.pdf" in output


def test_documents_belong_to_the_chosen_user_only(user, other_user, tmp_path, exported):
    do_import(load(tmp_path, exported), other_user)
    assert ExamDocument.objects.filter(user=other_user).count() == 2
    assert ExamDocument.objects.filter(user=user).count() == 0
    assert not ExamResult.objects.filter(user=user).exists()


# --- duplicados ------------------------------------------------------------------------------


def test_document_with_same_sha256_for_the_user_is_skipped_not_overwritten(
    user, other_user, tmp_path, exported
):
    existing = ExamDocument.objects.create(
        user=user,
        sha256=doc_of(exported, "b_simples.pdf")["sha256"],
        original_filename="ja_estava.pdf",
        status=DocumentStatus.COMPLETED,
    )
    ExamResult.objects.create(user=user, document=existing, name_raw="X", value_raw="1")

    output = do_import(load(tmp_path, exported), user)

    assert ExamDocument.objects.filter(user=user).count() == 2  # existing + a
    assert list(existing.results.values_list("name_raw", flat=True)) == ["X"]
    assert "pulados (já existem): 1" in output
    # outro usuário não é afetado pelo hash do primeiro
    do_import(load(tmp_path, exported), other_user)
    assert ExamDocument.objects.filter(user=other_user).count() == 2


def test_document_with_same_request_id_is_skipped_not_overwritten(user, tmp_path, exported):
    lab = Laboratory.objects.get_or_create(
        normalized_name=doc_of(exported, "a_completo.pdf")["laboratory"].lower(),
        defaults={"name": doc_of(exported, "a_completo.pdf")["laboratory"]},
    )[0]
    existing = ExamDocument.objects.create(
        user=user,
        laboratory=lab,
        sha256="f" * 64,
        original_filename="outro_arquivo.pdf",
        accession_number=doc_of(exported, "a_completo.pdf")["accession_number"],
        status=DocumentStatus.COMPLETED,
    )

    output = do_import(load(tmp_path, exported), user)

    assert not ExamDocument.objects.filter(original_filename="a_completo.pdf").exists()
    assert ExamDocument.objects.filter(user=user, pk=existing.pk).exists()
    assert ExamDocument.objects.filter(user=user).count() == 2  # existing + b
    assert "pulados (já existem): 1" in output


def test_two_documents_of_the_same_request_in_one_file_keep_the_first(user, tmp_path, exported):
    clone = copy.deepcopy(doc_of(exported, "b_simples.pdf"))
    clone["source_file"] = "b_copia.pdf"
    clone["sha256"] = "e" * 64
    exported["documents"].append(clone)

    do_import(load(tmp_path, exported), user)

    assert ExamDocument.objects.filter(user=user, accession_number="021-00002-002").count() == 1


def test_previous_values_already_known_for_that_day_are_not_duplicated(user, tmp_path, exported):
    do_import(load(tmp_path, exported), user)
    previous = ExamResult.objects.filter(user=user, origin="lab_previous", analyte__slug="glicose")
    keys = [(r.analyte_id, r.collected_at.date()) for r in previous]
    assert len(keys) == len(set(keys)) and keys

    # b e a trazem as mesmas "anteriores" de creatinina: só uma cópia de cada dia fica
    creatinine = ExamResult.objects.filter(
        user=user, origin="lab_previous", analyte__slug="creatinina"
    )
    days = [r.collected_at for r in creatinine]
    assert len(days) == len(set(days))


# --- JSON editado ------------------------------------------------------------------------------


def test_untouched_rows_are_imported_exactly_as_exported(user, tmp_path, exported):
    do_import(load(tmp_path, exported), user)
    row = ExamResult.objects.get(
        user=user,
        document__original_filename="b_simples.pdf",
        name_raw="CREATININA",
        origin="current",
    )
    assert row.review_status == ReviewStatus.AUTO
    assert row.value_numeric == Decimal("1.2") and row.flag == "normal"


def test_edited_value_raw_recomputes_numbers_flag_and_review_status(user, tmp_path, exported):
    row = row_of(doc_of(exported, "b_simples.pdf"), "creatinina")
    row["value_raw"] = "2,50"

    output = do_import(load(tmp_path, exported), user)

    saved = ExamResult.objects.get(
        user=user,
        document__original_filename="b_simples.pdf",
        analyte__slug="creatinina",
        origin="current",
    )
    assert saved.value_numeric == Decimal("2.5")
    assert saved.value_canonical == Decimal("2.5")
    assert saved.flag == "high"
    assert saved.review_status == ReviewStatus.USER_CORRECTED
    assert "reavaliados: 1" in output


def test_edited_normalized_number_without_touching_raw_is_overridden_by_raw(
    user, tmp_path, exported
):
    row = row_of(doc_of(exported, "b_simples.pdf"), "creatinina")
    row["value_numeric"] = "9"  # o impresso (value_raw) continua "1,20"
    row["flag"] = "low"

    do_import(load(tmp_path, exported), user)

    saved = ExamResult.objects.get(
        user=user,
        document__original_filename="b_simples.pdf",
        analyte__slug="creatinina",
        origin="current",
    )
    assert saved.value_numeric == Decimal("1.2") and saved.flag == "normal"


def test_edited_reference_and_unit_are_re_derived_from_raw(user, tmp_path, exported):
    row = row_of(doc_of(exported, "b_simples.pdf"), "creatinina")
    row["reference_raw"] = "0,50 a 1,00 mg/dL"

    do_import(load(tmp_path, exported), user)

    saved = ExamResult.objects.get(
        user=user,
        document__original_filename="b_simples.pdf",
        analyte__slug="creatinina",
        origin="current",
    )
    assert (saved.ref_low, saved.ref_high) == (Decimal("0.5"), Decimal("1"))
    assert saved.flag == "high"  # 1,20 > 1,00


def test_edit_with_unparseable_reference_warns_and_needs_review_when_value_missing(
    user, tmp_path, exported
):
    row = row_of(doc_of(exported, "b_simples.pdf"), "creatinina")
    row["value_raw"] = ""

    do_import(load(tmp_path, exported), user)

    saved = ExamResult.objects.get(
        user=user,
        document__original_filename="b_simples.pdf",
        analyte__slug="creatinina",
        origin="current",
    )
    assert saved.value_numeric is None
    assert "value_missing" in {w["code"] for w in saved.warnings}
    assert saved.review_status == ReviewStatus.NEEDS_REVIEW


def test_reviewed_true_imports_the_row_as_written(user, tmp_path, exported):
    row = row_of(doc_of(exported, "b_simples.pdf"), "creatinina")
    row["value_numeric"] = "9"
    row["flag"] = "abnormal"
    row["reviewed"] = True

    do_import(load(tmp_path, exported), user)

    saved = ExamResult.objects.get(
        user=user,
        document__original_filename="b_simples.pdf",
        analyte__slug="creatinina",
        origin="current",
    )
    assert saved.value_numeric == Decimal("9") and saved.flag == "abnormal"


def test_keep_false_and_deleted_rows_are_not_imported(user, tmp_path, exported):
    document = doc_of(exported, "b_simples.pdf")
    row_of(document, "creatinina")["keep"] = False
    document["results"].remove(row_of(document, "tsh"))
    kept_current = [
        r for r in document["results"] if r["origin"] == "current" and r["keep"] is not False
    ]

    output = do_import(load(tmp_path, exported), user)

    rows = ExamResult.objects.filter(user=user, document__original_filename="b_simples.pdf")
    assert rows.filter(origin="current").count() == len(kept_current)
    assert not rows.filter(analyte__slug="creatinina", origin="current").exists()
    assert not rows.filter(analyte__slug="tsh", origin="current").exists()
    assert rows.filter(analyte__slug="erfg", origin="current").exists()
    assert "linhas descartadas (keep=false): 1" in output


def test_document_with_keep_false_or_no_kept_results_is_skipped(user, tmp_path, exported):
    doc_of(exported, "a_completo.pdf")["keep"] = False
    for row in doc_of(exported, "b_simples.pdf")["results"]:
        row["keep"] = False

    output = do_import(load(tmp_path, exported), user)

    assert ExamDocument.objects.filter(user=user).count() == 0
    assert "pulados (keep=false): 1" in output and "pulados (sem resultados): 1" in output


def test_accepted_review_status_clears_needs_review_and_completes_the_document(
    user, tmp_path, exported
):
    document = doc_of(exported, "b_simples.pdf")
    for row in document["results"]:
        if row["review_status"] == "needs_review":
            row["review_status"] = "accepted"
    document["warnings"] = []

    do_import(load(tmp_path, exported), user)

    saved = ExamDocument.objects.get(user=user, original_filename="b_simples.pdf")
    assert not saved.results.filter(review_status=ReviewStatus.NEEDS_REVIEW).exists()
    assert saved.status == DocumentStatus.COMPLETED


def test_accepted_survives_an_edit_of_the_same_row(user, tmp_path, exported):
    row = row_of(doc_of(exported, "b_simples.pdf"), "creatinina")
    row["value_raw"] = ""
    row["review_status"] = "accepted"

    do_import(load(tmp_path, exported), user)

    saved = ExamResult.objects.get(
        user=user,
        document__original_filename="b_simples.pdf",
        analyte__slug="creatinina",
        origin="current",
    )
    assert saved.review_status == ReviewStatus.CONFIRMED


def test_status_stays_needs_review_while_rows_or_document_warnings_remain(user, tmp_path, exported):
    do_import(load(tmp_path, exported), user)
    # a_completo traz o aviso de laudo `llm_budget_exceeded`
    assert (
        ExamDocument.objects.get(user=user, original_filename="a_completo.pdf").status
        == DocumentStatus.NEEDS_REVIEW
    )


def test_row_added_by_hand_is_derived_from_its_raw_strings(user, tmp_path, exported):
    document = doc_of(exported, "b_simples.pdf")
    document["results"].append(
        {
            "name_raw": "Glicose",
            "analyte": "glicose",
            "value_raw": "130",
            "unit_raw": "mg/dL",
            "reference_raw": "70 a 99 mg/dL",
            "collected_at": document["results"][0]["collected_at"],
        }
    )

    do_import(load(tmp_path, exported), user)

    saved = ExamResult.objects.get(
        user=user,
        document__original_filename="b_simples.pdf",
        analyte__slug="glicose",
        origin="current",
    )
    assert saved.value_numeric == Decimal("130") and saved.flag == "high"
    assert saved.review_status == ReviewStatus.USER_CORRECTED


def test_edited_count_per_microliter_reads_the_dot_as_thousands(user, tmp_path, exported):
    document = doc_of(exported, "b_simples.pdf")
    document["results"].append(
        {
            "name_raw": "Leucócitos",
            "analyte": "leucocitos",
            "value_raw": "5.600",
            "unit_raw": "/uL",
            "collected_at": document["results"][0]["collected_at"],
        }
    )

    do_import(load(tmp_path, exported), user)

    saved = ExamResult.objects.get(
        user=user, document__original_filename="b_simples.pdf", analyte__slug="leucocitos"
    )
    assert saved.value_numeric == Decimal("5600") and saved.unit == "/µL"


def test_removing_the_slug_marks_the_row_unmapped_for_review(user, tmp_path, exported):
    row = row_of(doc_of(exported, "b_simples.pdf"), "creatinina")
    row["analyte"] = None  # desassocia: vira "sem exame", para revisão

    do_import(load(tmp_path, exported), user)

    saved = ExamResult.objects.get(
        user=user,
        document__original_filename="b_simples.pdf",
        name_raw="CREATININA",
        origin="current",
    )
    assert saved.analyte is None
    assert "unmapped_analyte" in {w["code"] for w in saved.warnings}
    assert saved.review_status == ReviewStatus.NEEDS_REVIEW


def test_document_header_fields_are_taken_as_written(user, tmp_path, exported):
    document = doc_of(exported, "b_simples.pdf")
    document["ordering_physician"] = "DRA EDITADA"
    document["collected_on"] = "2020-01-02"

    do_import(load(tmp_path, exported), user)

    saved = ExamDocument.objects.get(user=user, original_filename="b_simples.pdf")
    assert saved.ordering_physician == "DRA EDITADA"
    assert str(saved.collected_on) == "2020-01-02"


def test_page_text_round_trip_with_include_text(user, tmp_path):
    build_set(tmp_path / "laudos")
    data = export(tmp_path / "laudos", tmp_path / "t.json", "--include-text")

    do_import(load(tmp_path, data), user)

    saved = ExamDocument.objects.get(user=user, original_filename="b_simples.pdf")
    assert saved.page_text == doc_of(data, "b_simples.pdf")["page_text"]


# --- erros: nada é gravado -------------------------------------------------------------------


def test_unknown_analyte_slug_is_a_clear_error_and_writes_nothing(user, tmp_path, exported):
    row_of(doc_of(exported, "b_simples.pdf"), "creatinina")["analyte"] = "exame-que-nao-existe"
    before = db_counts()

    with pytest.raises(CommandError, match="exame-que-nao-existe"):
        do_import(load(tmp_path, exported), user)

    assert db_counts() == before


def test_analyte_missing_from_db_but_in_catalog_is_synced_first(user, tmp_path, exported):
    Analyte.objects.filter(slug="creatinina").delete()  # catálogo desatualizado no banco

    do_import(load(tmp_path, exported), user)

    assert Analyte.objects.filter(slug="creatinina").exists()
    assert ExamResult.objects.filter(user=user, analyte__slug="creatinina").exists()


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda d: d.update(format="myexams-reports/2"), "(?i)versão"),
        (lambda d: d.update(format="outra-coisa"), "(?i)formato"),
        (lambda d: d.pop("documents"), "documents"),
        (lambda d: d.update(documents={}), "documents"),
        (lambda d: d["documents"][0].pop("sha256"), "sha256"),
        (lambda d: d["documents"][0].update(sha256="xyz"), "sha256"),
        (lambda d: d["documents"][0].update(status="uploaded"), "status"),
        (lambda d: d["documents"][0].update(patient_birth_date="05/03/1977"), "patient_birth_date"),
        (lambda d: d["documents"][0]["results"][0].update(value_numeric="abc"), "value_numeric"),
        (lambda d: d["documents"][0]["results"][0].update(flag="bom"), "flag"),
        (lambda d: d["documents"][0]["results"][0].update(page="1"), "page"),
        (lambda d: d["documents"][0]["results"][0].update(name_raw=""), "name_raw"),
        (lambda d: d["documents"][0]["results"][0].update(collected_at="ontem"), "collected_at"),
        (lambda d: d["documents"][0]["results"][0].update(warnings="x"), "warnings"),
        (lambda d: d["documents"][0]["results"][0].update(value_raw="x" * 300), "value_raw"),
        (lambda d: d["documents"][1]["results"][0].update(flag="bom"), "documento 2"),
    ],
)
def test_malformed_files_are_rejected_with_a_clear_message_and_no_partial_writes(
    user, tmp_path, exported, mutate, message
):
    mutate(exported)
    before = db_counts()

    with pytest.raises(CommandError, match=message):
        do_import(load(tmp_path, exported), user)

    assert db_counts() == before  # nem o documento anterior ao defeituoso foi criado


def test_not_json_and_missing_file_are_clear_errors(user, tmp_path):
    bad = tmp_path / "ruim.json"
    bad.write_text("{isto nao e json")
    with pytest.raises(CommandError, match="JSON"):
        do_import(bad, user)
    with pytest.raises(CommandError, match="não encontrado"):
        do_import(tmp_path / "nao-existe.json", user)


def test_error_messages_never_echo_exam_values(user, tmp_path, exported):
    exported["documents"][0]["results"][0]["value_numeric"] = "VALOR-SECRETO-123"
    with pytest.raises(CommandError) as raised:
        do_import(load(tmp_path, exported), user)
    assert "VALOR-SECRETO-123" not in str(raised.value)


# --- simulação ----------------------------------------------------------------------------------


def test_dry_run_writes_nothing_and_reports_the_same_counts(user, tmp_path, exported):
    Analyte.objects.filter(slug="creatinina").delete()  # até o sync do catálogo é revertido
    before = db_counts()
    path = load(tmp_path, exported)

    dry = do_import(path, user, "--dry-run")

    assert db_counts() == before
    assert "simulação" in dry and "criados: 2" in dry
    real = do_import(path, user)
    assert "criados: 2" in real


def test_dry_run_still_validates_the_file(user, tmp_path, exported):
    exported["format"] = "myexams-reports/9"
    with pytest.raises(CommandError, match="(?i)versão"):
        do_import(load(tmp_path, exported), user, "--dry-run")


def test_user_resolution_errors(user, other_user, tmp_path, exported):
    path = load(tmp_path, exported)
    with pytest.raises(CommandError, match="Informe --email"):
        run_command("import_json", str(path))
    with pytest.raises(CommandError, match="não encontrado"):
        run_command("import_json", str(path), "--email", "ninguem@example.com")


def test_file_is_not_modified_by_import(user, tmp_path, exported):
    path = load(tmp_path, exported)
    before = path.read_bytes()
    do_import(path, user)
    assert path.read_bytes() == before
    assert json.loads(before)["format"] == "myexams-reports/1"
