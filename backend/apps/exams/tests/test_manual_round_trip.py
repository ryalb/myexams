"""Linhas `manual` na ponte JSON: o formato aceita a origem e a importação a preserva."""

import copy
from datetime import date

import pytest

from apps.exams import reports_json
from apps.exams.models import (
    Analyte,
    DocumentStatus,
    ExamDocument,
    ExamResult,
    ResultOrigin,
    ReviewStatus,
)

from .report_set import run_command, write_json

pytestmark = pytest.mark.django_db


def stored_document(user) -> ExamDocument:
    document = ExamDocument.objects.create(
        user=user,
        sha256="a" * 64,
        original_filename="laudo.pdf",
        status=DocumentStatus.COMPLETED,
        collected_on=date(2026, 8, 25),
        accession_number="777-1",
    )
    ExamResult.objects.create(
        user=user,
        document=document,
        name_raw="Glicose",
        value_raw="90",
        unit_raw="mg/dL",
        value_numeric=90,
        unit="mg/dL",
        analyte=Analyte.objects.get(slug="glicose"),
    )
    ExamResult.objects.create(
        user=user,
        document=document,
        origin=ResultOrigin.MANUAL,
        position=1,
        name_raw="Ureia",
        value_raw="30",
        unit_raw="mg/dL",
        value_numeric=30,
        unit="mg/dL",
        analyte=Analyte.objects.get(slug="ureia"),
        review_status=ReviewStatus.USER_CORRECTED,
    )
    return document


def exported(document: ExamDocument) -> dict:
    return reports_json.build_file([reports_json.serialize_document(document, source_file="a.pdf")])


def test_manual_origin_survives_export_and_import(user, other_user, tmp_path):
    document = stored_document(user)
    data = exported(document)
    assert [r["origin"] for r in data["documents"][0]["results"]] == ["current", "manual"]

    path = write_json(tmp_path / "out.json", data)
    ExamDocument.objects.all().delete()
    output = run_command("import_json", str(path), "--email", user.email)

    imported = ExamDocument.objects.get(user=user)
    rows = list(imported.results.order_by("position"))
    assert [r.origin for r in rows] == [ResultOrigin.CURRENT, ResultOrigin.MANUAL]
    assert rows[1].review_status == ReviewStatus.USER_CORRECTED
    assert rows[1].name_raw == "Ureia"
    assert "Ureia" not in output  # o terminal só tem contagens


def test_manual_row_with_edited_value_is_rederived_like_any_other_row(user, tmp_path):
    document = stored_document(user)
    data = exported(document)
    edited = copy.deepcopy(data)
    edited["documents"][0]["results"][1]["value_raw"] = "35,5"
    ExamDocument.objects.all().delete()

    run_command("import_json", str(write_json(tmp_path / "e.json", edited)), "--email", user.email)

    row = ExamResult.objects.get(origin=ResultOrigin.MANUAL)
    assert str(row.value_numeric) == "35.500000"
    assert row.review_status == ReviewStatus.USER_CORRECTED


def test_unknown_origin_is_still_rejected():
    data = {
        "format": reports_json.FORMAT,
        "documents": [
            {
                "source_file": "a.pdf",
                "sha256": "b" * 64,
                "status": "completed",
                "results": [{"name_raw": "X", "origin": "inventada"}],
            }
        ],
    }

    with pytest.raises(reports_json.ReportsFileError, match="origin"):
        reports_json.parse_file(data)
