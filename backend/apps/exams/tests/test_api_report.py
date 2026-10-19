"""GET /report/: dados do relatório executivo (Markdown no frontend). Dados inventados."""

import json
from datetime import date

import pytest
from rest_framework.test import APIClient

from apps.exams.models import Analyte, DocumentStatus, ExamDocument, ExamResult, ResultOrigin

pytestmark = pytest.mark.django_db

REPORT = "/api/v1/report/"
_counter = iter(range(1, 10_000))


@pytest.fixture
def api(user):
    client = APIClient()
    client.force_authenticate(user)
    return client


def add_result(user, collected_on, value, *, name="Glicose", origin=ResultOrigin.CURRENT):
    document = ExamDocument.objects.create(
        user=user,
        sha256=f"{next(_counter):064d}",
        original_filename="laudo.pdf",
        status=DocumentStatus.COMPLETED,
        collected_on=collected_on,
    )
    return ExamResult.objects.create(
        user=user,
        document=document,
        name_raw=name,
        value_raw=value,
        unit_raw="mg/dL",
        reference_raw="70 a 99 mg/dL",
        origin=origin,
        value_numeric=value,
        value_canonical=value,
        unit="mg/dL",
        ref_kind="range",
        ref_low=70,
        ref_high=99,
        flag="high" if float(value) > 99 else "normal",
        analyte=Analyte.objects.get(slug="glicose"),
    )


def test_requires_authentication():
    assert APIClient().get(REPORT).status_code == 401


def test_empty_report_has_profile_and_no_analytes(api):
    body = api.get(REPORT).json()

    assert body["analytes"] == []
    assert body["profile"]["sex"] == "male"
    assert isinstance(body["profile"]["age"], int)


def test_profile_exposes_age_never_birth_date_or_identity(api):
    text = json.dumps(api.get(REPORT).json())

    assert "1977" not in text and "birth" not in text
    assert "joao@example.com" not in text and "Teste da Silva" not in text


def test_groups_points_by_analyte_in_date_order(api, user):
    add_result(user, date(2026, 5, 1), "110")
    add_result(user, date(2025, 1, 10), "90")

    body = api.get(REPORT).json()

    assert len(body["analytes"]) == 1
    glicose = body["analytes"][0]
    assert glicose["slug"] == "glicose"
    assert [p["value_raw"] for p in glicose["points"]] == ["90", "110"]
    first = glicose["points"][0]
    assert (first["value"], first["ref_low"], first["ref_high"], first["flag"]) == (
        90.0,
        70.0,
        99.0,
        "normal",
    )


def test_only_the_users_own_results(api, other_user, user):
    add_result(other_user, date(2026, 1, 1), "300")

    assert api.get(REPORT).json()["analytes"] == []
    add_result(user, date(2026, 1, 1), "90")
    assert [p["value_raw"] for p in api.get(REPORT).json()["analytes"][0]["points"]] == ["90"]
