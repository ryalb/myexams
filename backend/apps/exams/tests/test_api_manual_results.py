"""POST/DELETE /exam-results/ (resultado digitado) e dismiss-warning. Dados inventados."""

import json
from datetime import date, datetime
from decimal import Decimal

import pytest
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken

from apps.exams.codes import WarningCode
from apps.exams.models import (
    Analyte,
    AnalyteAlias,
    DocumentStatus,
    ExamDocument,
    ExamResult,
    ResultOrigin,
    ReviewStatus,
)

pytestmark = pytest.mark.django_db

RESULTS = "/api/v1/exam-results/"
_counter = iter(range(1, 10_000))


@pytest.fixture
def api(user):
    client = APIClient()
    client.force_authenticate(user)
    return client


@pytest.fixture
def other_api(other_user):
    client = APIClient()
    client.force_authenticate(other_user)
    return client


def make_document(
    user, *, status=DocumentStatus.COMPLETED, collected_on=date(2026, 8, 25), **extra
):
    return ExamDocument.objects.create(
        user=user,
        sha256=f"{next(_counter):064d}",
        original_filename="laudo.pdf",
        status=status,
        collected_on=collected_on,
        **extra,
    )


def make_result(document, *, name="GLICOSE", value="90", position=0, page=1, **extra):
    return ExamResult.objects.create(
        user=document.user,
        document=document,
        name_raw=name,
        value_raw=value,
        position=position,
        page=page,
        **extra,
    )


def unread(page, title=None):
    params = {"page": page} if title is None else {"page": page, "title": title}
    return {"code": WarningCode.BLOCK_UNREADABLE.value, "params": params}


def post(api, document, **fields):
    body = {"document": document.pk, "name_raw": "Glicose", "value_raw": "95", **fields}
    return api.post(RESULTS, body, format="json")


# --- criação ------------------------------------------------------------------------------


def test_create_derives_everything_with_the_import_functions(api, user):
    document = make_document(user)
    make_result(document, position=3, page=2)

    response = post(
        api,
        document,
        name_raw="Glicose",
        value_raw="115,5",
        unit_raw="mg/dl",
        reference_raw="70 a 99 mg/dL",
        notes="digitado do papel",
    )

    assert response.status_code == 201
    body = response.json()
    assert body["origin"] == "manual"
    assert body["document"] == document.pk
    assert body["analyte_info"]["slug"] == "glicose"  # resolvido pelo catálogo
    assert (body["value_raw"], body["value_numeric"], body["comparator"]) == (
        "115,5",
        "115.500000",
        "eq",
    )
    assert body["unit"] == "mg/dL"  # grafia canônica
    assert body["ref_kind"] == "range" and body["ref_low"] == "70.000000"
    assert body["flag"] == "high"
    assert body["review_status"] == ReviewStatus.USER_CORRECTED
    assert body["warnings"] == []
    assert body["notes"] == "digitado do papel"
    # Depois do último resultado do laudo (posição e página).
    assert (body["position"], body["page"]) == (4, 2)

    row = ExamResult.objects.get(pk=body["id"])
    assert row.user == user and row.value_canonical == Decimal("115.5")
    assert row.collected_at is not None
    assert timezone.localtime(row.collected_at).date() == document.collected_on
    assert row.raw == {"source": "manual"}


def test_create_accepts_comparators_dot_decimals_and_qualitative_vocabulary(api, user):
    document = make_document(user)

    lt = post(api, document, name_raw="PSA", value_raw="<0,5", unit_raw="ng/mL").json()
    dot = post(api, document, name_raw="Insulina", value_raw="12.4", unit_raw="µUI/mL").json()
    word = post(api, document, name_raw="Nitrito", value_raw="  nao   reagente ").json()

    assert (lt["comparator"], lt["value_numeric"]) == ("lt", "0.500000")
    assert (dot["comparator"], dot["value_numeric"]) == ("eq", "12.400000")
    assert (word["value_numeric"], word["value_text"]) == (None, "nao reagente")
    assert word["value_raw"] == "nao reagente"  # espaços colapsados


def test_ambiguous_number_follows_the_existing_warning_and_asks_for_review(api, user):
    document = make_document(user, status=DocumentStatus.COMPLETED)

    body = post(api, document, name_raw="Glicose", value_raw="1.234", unit_raw="mg/dL").json()

    assert WarningCode.AMBIGUOUS_NUMBER_FORMAT.value in [w["code"] for w in body["warnings"]]
    assert body["review_status"] == ReviewStatus.NEEDS_REVIEW
    document.refresh_from_db()
    assert document.status == DocumentStatus.NEEDS_REVIEW


def test_unmapped_name_stays_null_with_the_usual_warning(api, user):
    document = make_document(user)

    body = post(api, document, name_raw="Exame Inventado Xyz", value_raw="3", unit_raw="U").json()

    assert body["analyte"] is None
    assert [w["code"] for w in body["warnings"]] == ["unmapped_analyte"]
    assert body["review_status"] == ReviewStatus.NEEDS_REVIEW


def test_unknown_unit_is_only_a_warning(api, user):
    document = make_document(user)

    response = post(api, document, name_raw="Glicose", value_raw="90", unit_raw="bananas")

    assert response.status_code == 201
    assert response.json()["warnings"][0]["code"] == "unit_not_convertible"


def test_explicit_analyte_wins_and_teaches_a_personal_alias(api, user):
    document = make_document(user)
    tsh = Analyte.objects.get(slug="tsh")

    body = post(
        api, document, name_raw="Hormonio estimulante", value_raw="2,1", unit_raw="µUI/mL",
        analyte=tsh.pk,
    ).json()  # fmt: skip

    assert body["analyte"] == tsh.pk
    alias = AnalyteAlias.objects.get(user=user, alias_normalized="hormonio estimulante")
    assert alias.analyte == tsh and alias.source == "user"


def test_collected_at_defaults_to_document_date_and_accepts_date_or_datetime(api, user):
    document = make_document(user)
    other_day = post(api, document, name_raw="Ureia", value_raw="30", collected_at="2026-07-01")
    exact = post(
        api, document, name_raw="Sodio", value_raw="140", collected_at="2026-07-01T08:30:00-03:00"
    )

    day = ExamResult.objects.get(pk=other_day.json()["id"]).collected_at
    moment = ExamResult.objects.get(pk=exact.json()["id"]).collected_at
    assert timezone.localtime(day).date() == date(2026, 7, 1)
    assert timezone.localtime(moment) == timezone.localtime(
        datetime.fromisoformat("2026-07-01T08:30:00-03:00")
    )


def test_document_without_collection_date_leaves_collected_at_empty(api, user):
    document = make_document(user, collected_on=None)

    body = post(api, document).json()

    assert body["collected_at"] is None


def test_position_starts_at_zero_in_an_empty_document(api, user):
    document = make_document(user)

    assert post(api, document).json()["position"] == 0


def test_document_counters_and_status_follow_the_new_row(api, user):
    document = make_document(user, status=DocumentStatus.NEEDS_REVIEW, warnings=[unread(1, "X")])
    make_result(document, review_status=ReviewStatus.AUTO)

    post(api, document, name_raw="Glicose", value_raw="150", unit_raw="mg/dL",
         reference_raw="70 a 99 mg/dL")  # fmt: skip

    detail = api.get(f"/api/v1/exam-documents/{document.pk}/").json()
    assert detail["result_count"] == 2
    assert detail["out_of_range_count"] == 1
    assert detail["review_count"] == 0
    assert detail["status"] == "needs_review"  # o aviso do laudo continua


def test_manual_row_that_needs_review_counts_for_the_document(api, user):
    document = make_document(user, status=DocumentStatus.COMPLETED)

    post(api, document, name_raw="Exame Inventado Xyz", value_raw="3", unit_raw="U")

    detail = api.get(f"/api/v1/exam-documents/{document.pk}/").json()
    assert detail["review_count"] == 1 and detail["status"] == "needs_review"
    listing = api.get("/api/v1/exam-documents/?status=needs_review").json()["results"]
    assert [d["id"] for d in listing] == [document.pk]


# --- validação ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("fields", "field", "code"),
    [
        ({"name_raw": "   "}, "name_raw", "result_name_required"),
        ({"value_raw": ""}, "value_raw", "result_value_required"),
        ({"value_raw": "  "}, "value_raw", "result_value_required"),
        ({"value_raw": "abc"}, "value_raw", "result_value_invalid"),
        ({"value_raw": "12 mg/dL"}, "value_raw", "result_value_invalid"),
        ({"value_raw": "9" * 20}, "value_raw", "result_value_invalid"),
    ],
)
def test_validation_errors_are_codes_on_the_field(api, user, fields, field, code):
    document = make_document(user)

    response = post(api, document, **fields)

    assert response.status_code == 400
    assert response.json()[field] == [code]
    assert not ExamResult.objects.filter(origin=ResultOrigin.MANUAL).exists()


def test_missing_name_and_value_are_the_same_coded_errors(api, user):
    document = make_document(user)

    response = api.post(RESULTS, {"document": document.pk}, format="json")

    assert response.status_code == 400
    assert response.json()["name_raw"] == ["result_name_required"]
    assert response.json()["value_raw"] == ["result_value_required"]


def test_length_limits_are_enforced(api, user):
    document = make_document(user)

    assert post(api, document, name_raw="N" * 256).status_code == 400
    assert post(api, document, unit_raw="u" * 61).status_code == 400
    assert post(api, document, value_raw="1" * 256).status_code == 400


def test_absurd_reference_number_is_a_coded_field_error_not_a_500(api, user):
    document = make_document(user)

    response = post(api, document, reference_raw=f"{'9' * 30} a {'9' * 31}")

    assert response.status_code == 400
    assert response.json()["reference_raw"] == ["result_reference_invalid"]


def test_document_is_required_and_must_exist(api, user):
    assert api.post(RESULTS, {"name_raw": "A", "value_raw": "1"}, format="json").status_code == 400
    assert post(api, make_document(user)).status_code == 201
    missing = api.post(
        RESULTS, {"document": 999999, "name_raw": "A", "value_raw": "1"}, format="json"
    )
    assert missing.status_code == 400 and "document" in missing.json()


@pytest.mark.parametrize("busy", [DocumentStatus.UPLOADED, DocumentStatus.EXTRACTING])
def test_document_still_being_read_is_refused_with_a_code(api, user, busy):
    document = make_document(user, status=busy)

    response = post(api, document)

    assert response.status_code == 409
    assert response.json() == {"code": "document_busy", "params": {}}
    assert not document.results.exists()


def test_requires_authentication(user):
    document = make_document(user)

    response = APIClient().post(RESULTS, {"document": document.pk}, format="json")

    assert response.status_code == 401


# --- escopo por usuário -------------------------------------------------------------------


def test_other_users_document_is_rejected_and_untouched(other_api, user):
    document = make_document(user)

    response = post(other_api, document)

    assert response.status_code == 400 and "document" in response.json()
    assert not document.results.exists()


# --- duplicidade --------------------------------------------------------------------------


def test_same_name_day_and_value_is_a_409_with_the_existing_id(api, user):
    document = make_document(user)
    first = post(api, document, name_raw="Glicose", value_raw="95").json()

    again = post(api, document, name_raw="  GLICOSE ", value_raw="95")

    assert again.status_code == 409
    assert again.json() == {"code": "result_duplicate", "params": {"result_id": first["id"]}}
    assert document.results.count() == 1


def test_duplicate_guard_also_catches_a_row_read_from_the_pdf(api, user):
    document = make_document(user)
    day = timezone.make_aware(datetime(2026, 8, 25, 7, 1))
    make_result(document, name="Glicose", value="95", collected_at=day)

    assert post(api, document, name_raw="GLICOSE", value_raw="95").status_code == 409


@pytest.mark.parametrize(
    "different",
    [{"value_raw": "96"}, {"name_raw": "Ureia"}, {"collected_at": "2026-08-26"}],
)
def test_different_value_name_or_day_is_not_a_duplicate(api, user, different):
    document = make_document(user)
    assert post(api, document, name_raw="Glicose", value_raw="95").status_code == 201

    assert post(api, document, **different).status_code == 201


def test_same_row_in_another_document_is_not_a_duplicate(api, user):
    first, second = make_document(user), make_document(user)

    assert post(api, first).status_code == 201
    assert post(api, second).status_code == 201


# --- listas e agregados -------------------------------------------------------------------


def test_manual_rows_show_in_lists_filters_history_and_overview(api, user):
    document = make_document(user)
    manual = post(api, document, name_raw="Glicose", value_raw="95", unit_raw="mg/dL").json()

    by_origin = api.get(f"{RESULTS}?origin=manual").json()["results"]
    in_document = api.get(f"/api/v1/exam-documents/{document.pk}/results/").json()
    history = api.get("/api/v1/analytes/glicose/history/").json()
    overview = api.get("/api/v1/overview/")

    assert [r["id"] for r in by_origin] == [manual["id"]]
    assert [r["id"] for r in in_document] == [manual["id"]]
    assert [p["origin"] for p in history["points"]] == ["manual"]
    assert overview.status_code == 200
    assert [row["slug"] for row in overview.json()] == ["glicose"]


def test_manual_row_does_not_disturb_the_previous_logic(api, user):
    document = make_document(user)
    old_day = timezone.make_aware(datetime(2020, 1, 5))
    previous = make_result(
        document, name="Glicose", value="80", origin=ResultOrigin.LAB_PREVIOUS, collected_at=old_day
    )

    post(api, document, name_raw="Glicose", value_raw="95")

    previous.refresh_from_db()
    assert previous.origin == ResultOrigin.LAB_PREVIOUS
    detail = api.get(f"/api/v1/exam-documents/{document.pk}/").json()
    assert detail["result_count"] == 1  # o anterior impresso continua fora da contagem


def test_graphql_counts_include_manual_rows(api, user):
    document = make_document(user)
    post(api, document, unit_raw="mg/dL")

    response = APIClient().post(
        "/graphql/",
        data=json.dumps({"query": "{ examDocuments { resultCount reviewCount } }"}),
        content_type="application/json",
        HTTP_AUTHORIZATION=f"Bearer {AccessToken.for_user(user)}",
    )

    assert response.status_code == 200
    assert response.json()["data"]["examDocuments"] == [{"resultCount": 1, "reviewCount": 0}]


# --- edição de linha manual ---------------------------------------------------------------


def test_patch_still_works_on_manual_rows(api, user):
    document = make_document(user)
    created = post(api, document, name_raw="Glicose", value_raw="95", unit_raw="mg/dL").json()

    response = api.patch(f"{RESULTS}{created['id']}/", {"value_raw": "101"}, format="json")

    assert response.status_code == 200
    row = ExamResult.objects.get(pk=created["id"])
    assert row.value_numeric == Decimal("101") and row.origin == ResultOrigin.MANUAL


# --- exclusão -----------------------------------------------------------------------------


def test_delete_manual_row_updates_status_and_counters(api, user):
    document = make_document(user, status=DocumentStatus.COMPLETED)
    make_result(document, review_status=ReviewStatus.AUTO)
    created = post(
        api, document, name_raw="Exame Inventado Xyz", value_raw="3", unit_raw="U"
    ).json()
    document.refresh_from_db()
    assert document.status == DocumentStatus.NEEDS_REVIEW

    response = api.delete(f"{RESULTS}{created['id']}/")

    assert response.status_code == 204
    assert not ExamResult.objects.filter(pk=created["id"]).exists()
    document.refresh_from_db()
    assert document.status == DocumentStatus.COMPLETED
    assert api.get(f"/api/v1/exam-documents/{document.pk}/").json()["result_count"] == 1


@pytest.mark.parametrize("origin", [ResultOrigin.CURRENT, ResultOrigin.LAB_PREVIOUS])
def test_delete_of_a_row_read_from_the_pdf_is_forbidden(api, user, origin):
    document = make_document(user)
    row = make_result(document, origin=origin)

    response = api.delete(f"{RESULTS}{row.pk}/")

    assert response.status_code == 403
    assert response.json() == {"code": "result_not_manual", "params": {}}
    assert ExamResult.objects.filter(pk=row.pk).exists()


def test_delete_of_someone_elses_manual_row_is_404(api, other_api, user):
    document = make_document(user)
    created = post(api, document).json()

    assert other_api.delete(f"{RESULTS}{created['id']}/").status_code == 404
    assert ExamResult.objects.filter(pk=created["id"]).exists()


def test_delete_requires_authentication(user):
    row = make_result(make_document(user), origin=ResultOrigin.MANUAL)

    assert APIClient().delete(f"{RESULTS}{row.pk}/").status_code == 401


def test_put_is_still_not_allowed(api, user):
    row = make_result(make_document(user), origin=ResultOrigin.MANUAL)

    assert api.put(f"{RESULTS}{row.pk}/", {}, format="json").status_code == 405


def test_reprocess_is_blocked_while_manual_rows_exist(api, user):
    document = make_document(user, page_text=[{"number": 1, "method": "text", "lines": ["x"]}])
    post(api, document, name_raw="Exame Inventado Xyz", value_raw="3", unit_raw="U")

    response = api.post(f"/api/v1/exam-documents/{document.pk}/reprocess/")

    assert response.status_code == 409
    assert response.json()["code"] == "document_not_reprocessable"


# --- dismiss-warning ----------------------------------------------------------------------


def dismiss(api, document, **body):
    return api.post(f"/api/v1/exam-documents/{document.pk}/dismiss-warning/", body, format="json")


def test_dismiss_removes_exactly_that_warning_and_refreshes_status(api, user):
    other = {"code": WarningCode.PATIENT_MISMATCH.value, "params": {}}
    document = make_document(
        user,
        status=DocumentStatus.NEEDS_REVIEW,
        warnings=[unread(2, "TEMPO DE PROTROMBINA"), unread(2, "IGF-1"), unread(3, "OUTRO"), other],
    )

    response = dismiss(api, document, code="block_unreadable", page=2, title="IGF-1")

    assert response.status_code == 200
    assert response.json()["warnings"] == [
        unread(2, "TEMPO DE PROTROMBINA"),
        unread(3, "OUTRO"),
        other,
    ]
    document.refresh_from_db()
    assert document.warnings == [unread(2, "TEMPO DE PROTROMBINA"), unread(3, "OUTRO"), other]
    assert document.status == DocumentStatus.NEEDS_REVIEW


def test_dismissing_the_last_warning_completes_the_document(api, user):
    document = make_document(
        user, status=DocumentStatus.NEEDS_REVIEW, warnings=[unread(1, "IGF-1")]
    )
    make_result(document, review_status=ReviewStatus.AUTO)

    response = dismiss(api, document, code="block_unreadable", page=1, title="IGF-1")

    assert response.json()["warnings"] == [] and response.json()["status"] == "completed"


def test_dismiss_without_title_removes_the_untitled_legacy_warning_and_page_matches(api, user):
    document = make_document(user, warnings=[unread(4), unread(5, "X")])

    dismiss(api, document, code="block_unreadable", page=4)

    document.refresh_from_db()
    assert document.warnings == [unread(5, "X")]


def test_dismiss_is_idempotent(api, user):
    document = make_document(user, warnings=[unread(2, "IGF-1")])

    first = dismiss(api, document, code="block_unreadable", page=2, title="IGF-1")
    second = dismiss(api, document, code="block_unreadable", page=2, title="IGF-1")

    assert first.status_code == second.status_code == 200
    assert second.json()["warnings"] == []


def test_dismiss_with_a_title_does_not_touch_a_different_title(api, user):
    document = make_document(user, warnings=[unread(2, "IGF-1")])

    dismiss(api, document, code="block_unreadable", page=2, title="OUTRO")

    document.refresh_from_db()
    assert document.warnings == [unread(2, "IGF-1")]


def test_only_block_unreadable_can_be_dismissed(api, user):
    mismatch = {"code": WarningCode.PATIENT_MISMATCH.value, "params": {}}
    document = make_document(user, warnings=[mismatch])

    response = dismiss(api, document, code="patient_mismatch", page=1)

    assert response.status_code == 400
    assert response.json() == {"code": "warning_not_dismissible", "params": {}}
    document.refresh_from_db()
    assert document.warnings == [mismatch]


def test_dismiss_validates_the_body(api, user):
    document = make_document(user, warnings=[unread(1, "X")])

    assert dismiss(api, document, code="block_unreadable").status_code == 400
    assert dismiss(api, document, code="block_unreadable", page=0).status_code == 400
    assert dismiss(api, document, page=1).status_code == 400


def test_dismiss_is_owner_only(other_api, user):
    document = make_document(user, warnings=[unread(1, "X")])

    response = dismiss(other_api, document, code="block_unreadable", page=1, title="X")

    assert response.status_code == 404
    document.refresh_from_db()
    assert document.warnings == [unread(1, "X")]


@pytest.mark.parametrize("busy", [DocumentStatus.UPLOADED, DocumentStatus.EXTRACTING])
def test_dismiss_is_refused_while_the_document_is_being_read(api, user, busy):
    document = make_document(user, status=busy, warnings=[unread(1, "X")])

    response = dismiss(api, document, code="block_unreadable", page=1, title="X")

    assert response.status_code == 409 and response.json()["code"] == "document_busy"
    document.refresh_from_db()
    assert document.warnings == [unread(1, "X")]
