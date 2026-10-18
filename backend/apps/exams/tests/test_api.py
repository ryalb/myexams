import json
from decimal import Decimal

import pytest
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken

from apps.exams import tasks, views
from apps.exams.extraction.fakes import FakeLLMClient
from apps.exams.models import (
    Analyte,
    AnalyteAlias,
    DocumentStatus,
    ExamDocument,
    ExamResult,
    ResultOrigin,
    ReviewStatus,
)

from .conftest import SAMPLE_HEADER, create_document, sample_llm_results

pytestmark = pytest.mark.django_db

PDF_BYTES = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF"


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


@pytest.fixture
def enqueued(monkeypatch):
    calls = []

    class Stub:
        @staticmethod
        def delay(*args):
            calls.append(args)

    monkeypatch.setattr(views, "process_exam_document", Stub)
    return calls


@pytest.fixture
def processed(user, sample_pdf, monkeypatch):
    llm = FakeLLMClient(header=SAMPLE_HEADER, results=sample_llm_results)
    monkeypatch.setattr(tasks, "build_llm_client", lambda: llm)
    document = create_document(user, sample_pdf)
    tasks.process_exam_document(document.pk)
    document.refresh_from_db()
    return document


def upload(client, content=PDF_BYTES, name="laudo.pdf"):
    from django.core.files.uploadedfile import SimpleUploadedFile

    return client.post(
        "/api/v1/exam-documents/",
        {"file": SimpleUploadedFile(name, content, content_type="application/pdf")},
        format="multipart",
    )


# --- upload ---------------------------------------------------------------


def test_upload_requires_authentication():
    response = APIClient().post("/api/v1/exam-documents/", {}, format="multipart")
    assert response.status_code == 401


def test_upload_without_file_returns_code(api):
    response = api.post("/api/v1/exam-documents/", {}, format="multipart")
    assert response.status_code == 400
    assert response.json() == {"code": "file_missing", "params": {}}


def test_upload_rejects_non_pdf(api):
    response = upload(api, b"nao sou pdf")
    assert response.status_code == 415
    assert response.json()["code"] == "file_not_pdf"


def test_upload_rejects_large_file(api, settings):
    settings.EXAMS_MAX_UPLOAD_BYTES = 10
    response = upload(api)
    assert response.status_code == 413
    assert response.json()["code"] == "file_too_large"


def test_upload_does_not_parse_the_pdf_in_the_request(api, enqueued):
    # PDF corrompido é aceito aqui; a falha acontece depois, no worker.
    response = upload(api, b"%PDF-1.4 quebrado")
    assert response.status_code == 202
    assert response.json()["page_count"] == 0


def test_upload_creates_document_and_enqueues(
    api, user, sample_pdf, enqueued, settings, django_capture_on_commit_callbacks
):
    with django_capture_on_commit_callbacks(execute=True):
        response = upload(api, sample_pdf.read_bytes())
    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "uploaded" and body["page_count"] == 0
    document = ExamDocument.objects.get(pk=body["id"])
    assert document.user == user and document.upload_path
    assert enqueued == [(document.pk,)]
    # arquivo temporário fica em diretório privado com permissão restrita
    assert (
        oct(document.upload_path and __import__("os").stat(document.upload_path).st_mode)[-3:]
        == "600"
    )


def test_duplicate_upload_is_rejected_and_temp_file_removed(
    api, sample_pdf, enqueued, settings, django_capture_on_commit_callbacks
):
    with django_capture_on_commit_callbacks(execute=True):
        upload(api, sample_pdf.read_bytes())
        again = upload(api, sample_pdf.read_bytes())
    assert again.status_code == 409
    assert again.json()["code"] == "duplicate_document"
    assert "date" in again.json()["params"]
    assert len(list(settings.EXAMS_UPLOAD_DIR.glob("*"))) == 1


# --- escopo multiusuário ---------------------------------------------------


def test_documents_and_results_are_scoped_per_user(api, other_api, processed):
    assert api.get("/api/v1/exam-documents/").json()["count"] == 1
    assert other_api.get("/api/v1/exam-documents/").json()["count"] == 0
    assert other_api.get(f"/api/v1/exam-documents/{processed.pk}/").status_code == 404
    assert other_api.get(f"/api/v1/exam-documents/{processed.pk}/results/").status_code == 404
    assert other_api.delete(f"/api/v1/exam-documents/{processed.pk}/").status_code == 404
    assert other_api.post(f"/api/v1/exam-documents/{processed.pk}/confirm/").status_code == 404

    result = processed.results.first()
    assert other_api.get("/api/v1/exam-results/").json()["count"] == 0
    assert other_api.get(f"/api/v1/exam-results/{result.pk}/").status_code == 404
    assert (
        other_api.patch(
            f"/api/v1/exam-results/{result.pk}/", {"value_raw": "1"}, format="json"
        ).status_code
        == 404
    )


def test_history_and_overview_only_show_own_data(api, other_api, processed):
    assert len(api.get("/api/v1/analytes/glicose/history/").json()["points"]) == 3
    assert other_api.get("/api/v1/analytes/glicose/history/").json()["points"] == []
    assert other_api.get("/api/v1/overview/").json() == []


def test_anonymous_cannot_read_anything():
    for path in (
        "/api/v1/exam-documents/",
        "/api/v1/exam-results/",
        "/api/v1/overview/",
        "/api/v1/analytes/",
        "/api/v1/profile/",
    ):
        assert APIClient().get(path).status_code == 401


# --- listagem, histórico e visão geral ------------------------------------


def test_document_list_has_counts_and_filters(api, processed):
    body = api.get("/api/v1/exam-documents/").json()
    item = body["results"][0]
    assert item["laboratory_name"] == "Exemplo"
    assert item["result_count"] == 11
    assert item["review_count"] >= 1  # hemoglobina sem valor
    assert item["out_of_range_count"] >= 2  # glicose (alta) e eRFG (baixo)
    assert api.get("/api/v1/exam-documents/?out_of_range=1").json()["count"] == 1
    assert api.get("/api/v1/exam-documents/?status=failed").json()["count"] == 0
    assert api.get("/api/v1/exam-documents/?collected_from=2027-01-01").json()["count"] == 0


def test_document_results_endpoint(api, processed):
    rows = api.get(f"/api/v1/exam-documents/{processed.pk}/results/").json()
    names = {r["name_raw"] for r in rows if r["origin"] == "current"}
    assert {"GLICOSE", "Hemácias", "CREATININA"} <= names
    glucose = next(r for r in rows if r["name_raw"] == "GLICOSE" and r["origin"] == "current")
    assert glucose["analyte_info"]["slug"] == "glicose"
    assert glucose["flag"] == "high"


def test_analyte_history_is_chronological_with_reference_bands(api, processed):
    data = api.get("/api/v1/analytes/glicose/history/").json()
    assert data["chartable"] is True
    assert data["analyte"]["unit"] == "mg/dL"
    values = [p["value"] for p in data["points"]]
    assert values == [84.0, 92.0, 103.0]  # 2018, 2022, 2026
    dates = [p["collected_at"] for p in data["points"]]
    assert dates == sorted(dates)
    last = data["points"][-1]
    assert last["origin"] == "current" and last["ref_low"] == 70.0 and last["ref_high"] == 99.0
    assert last["interpretation"] == "Glicemia de jejum alterada"
    assert data["points"][0]["origin"] == "lab_previous" and data["points"][0]["ref_low"] is None


def test_overview_has_latest_delta_and_sparkline(api, processed):
    overview = {row["slug"]: row for row in api.get("/api/v1/overview/").json()}
    glucose = overview["glicose"]
    assert glucose["latest_value"] == 103.0
    assert glucose["delta"] == pytest.approx(11.0)  # 103 - 92
    assert glucose["delta_pct"] == pytest.approx(11 / 92 * 100)
    assert glucose["sparkline"] == [84.0, 92.0, 103.0]
    assert glucose["latest_flag"] == "high"
    assert glucose["count"] == 3
    assert overview["creatinina"]["category"] == "kidney"


def test_unknown_analyte_history_is_404(api, processed):
    assert api.get("/api/v1/analytes/nao-existe/history/").status_code == 404


# --- edição e revisão -------------------------------------------------------


def test_editing_a_value_recomputes_and_marks_corrected(api, processed):
    hemoglobin = processed.results.get(name_raw="Hemoglobina")
    response = api.patch(
        f"/api/v1/exam-results/{hemoglobin.pk}/", {"value_raw": "12,9"}, format="json"
    )
    assert response.status_code == 200
    hemoglobin.refresh_from_db()
    assert hemoglobin.value_numeric == Decimal("12.9")
    assert hemoglobin.flag == "low"  # 12,9 < 13,50
    assert hemoglobin.review_status == ReviewStatus.USER_CORRECTED
    assert hemoglobin.warnings == []


def test_mapping_an_analyte_teaches_an_alias(api, processed):
    result = processed.results.get(name_raw="MAGNÉSIO")
    wrong = Analyte.objects.get(slug="calcio")
    api.patch(f"/api/v1/exam-results/{result.pk}/", {"analyte": wrong.pk}, format="json")
    alias = AnalyteAlias.objects.get(alias_normalized="magnesio", user=processed.user)
    assert alias.analyte == wrong and alias.source == "user"
    # o apelido global do catálogo não é alterado
    seed = AnalyteAlias.objects.get(alias_normalized="magnesio", user__isnull=True)
    assert seed.analyte.slug == "magnesio" and seed.source == "seed"


def test_only_confirmed_review_status_can_be_set(api, processed):
    result = processed.results.first()
    bad = api.patch(f"/api/v1/exam-results/{result.pk}/", {"review_status": "auto"}, format="json")
    assert bad.status_code == 400
    ok = api.patch(
        f"/api/v1/exam-results/{result.pk}/", {"review_status": "confirmed"}, format="json"
    )
    assert ok.status_code == 200
    result.refresh_from_db()
    assert result.review_status == ReviewStatus.CONFIRMED


def test_read_only_fields_cannot_be_changed(api, processed):
    result = processed.results.get(name_raw="GLICOSE", origin="current")
    api.patch(
        f"/api/v1/exam-results/{result.pk}/", {"flag": "low", "value_numeric": "1"}, format="json"
    )
    result.refresh_from_db()
    assert result.flag == "high" and result.value_numeric == Decimal("103")


def test_confirm_clears_warnings_and_completes_document(api, processed):
    assert processed.status == DocumentStatus.NEEDS_REVIEW
    response = api.post(f"/api/v1/exam-documents/{processed.pk}/confirm/")
    assert response.status_code == 200
    assert response.json()["status"] == "completed"
    assert not processed.results.filter(review_status=ReviewStatus.NEEDS_REVIEW).exists()


def test_reprocess_is_blocked_after_user_edits(
    api, processed, enqueued, django_capture_on_commit_callbacks
):
    with django_capture_on_commit_callbacks(execute=True):
        assert api.post(f"/api/v1/exam-documents/{processed.pk}/reprocess/").status_code == 202
    assert enqueued == [(processed.pk, True)]
    api.post(f"/api/v1/exam-documents/{processed.pk}/confirm/")
    blocked = api.post(f"/api/v1/exam-documents/{processed.pk}/reprocess/")
    assert blocked.status_code == 409
    assert blocked.json()["code"] == "document_not_reprocessable"


def test_deleting_a_document_removes_its_results(api, processed):
    assert api.delete(f"/api/v1/exam-documents/{processed.pk}/").status_code == 204
    assert not ExamResult.objects.filter(document_id=processed.pk).exists()


def test_results_filters(api, processed):
    assert api.get("/api/v1/exam-results/?analyte=glicose").json()["count"] == 3
    assert api.get("/api/v1/exam-results/?origin=lab_previous").json()["count"] > 0
    current = api.get("/api/v1/exam-results/?document=%d&review_status=needs_review" % processed.pk)
    assert current.json()["count"] >= 1
    assert all(r["origin"] in ("current", "lab_previous") for r in current.json()["results"])
    assert ResultOrigin.CURRENT == "current"


# --- autenticação, registro e perfil --------------------------------------


def test_registration_is_disabled_by_default(settings):
    settings.ALLOW_PUBLIC_REGISTRATION = False  # o .env local pode ligar o registro
    response = APIClient().post(
        "/api/v1/auth/register/", {"email": "a@b.com", "password": "senha-forte-123"}, format="json"
    )
    assert response.status_code == 404
    assert response.json()["code"] == "registration_disabled"


def test_registration_when_enabled(settings):
    settings.ALLOW_PUBLIC_REGISTRATION = True
    payload = {"email": "novo@example.com", "password": "senha-forte-123"}
    assert APIClient().post("/api/v1/auth/register/", payload, format="json").status_code == 200
    duplicate = APIClient().post("/api/v1/auth/register/", payload, format="json")
    assert duplicate.status_code == 400
    assert duplicate.json() == {"email": ["email_already_registered"]}


def test_profile_roundtrip_and_me(api):
    assert (
        api.put(
            "/api/v1/profile/", {"sex": "female", "birth_date": "1990-01-02"}, format="json"
        ).status_code
        == 200
    )
    me = api.get("/api/v1/auth/me/").json()
    assert "avatar_url" not in me
    assert me["profile"] == {"sex": "female", "birth_date": "1990-01-02"}
    assert api.put("/api/v1/profile/", {"sex": "x"}, format="json").status_code == 400


# --- GraphQL ----------------------------------------------------------------

OVERVIEW_QUERY = "{ analytesOverview { slug latestValue latestFlag sparkline } }"


def graphql(query, token=None):
    headers = {"HTTP_AUTHORIZATION": f"Bearer {token}"} if token else {}
    return APIClient().post(
        "/graphql/", data=json.dumps({"query": query}), content_type="application/json", **headers
    )


def test_graphql_overview_with_bearer_token(user, processed):
    response = graphql(OVERVIEW_QUERY, str(AccessToken.for_user(user)))
    assert response.status_code == 200
    rows = {r["slug"]: r for r in response.json()["data"]["analytesOverview"]}
    assert rows["glicose"]["latestValue"] == 103.0 and rows["glicose"]["latestFlag"] == "high"


def test_graphql_history_and_documents(user, other_user, processed):
    token = str(AccessToken.for_user(user))
    query = (
        '{ analyteHistory(slug: "glicose") { chartable points { value origin refHigh } } '
        "examDocuments { id status resultCount laboratoryName } }"
    )
    data = graphql(query, token).json()["data"]
    assert [p["value"] for p in data["analyteHistory"]["points"]] == [84.0, 92.0, 103.0]
    assert data["examDocuments"][0]["resultCount"] == 11
    other = graphql(query, str(AccessToken.for_user(other_user))).json()["data"]
    assert other["examDocuments"] == [] and other["analyteHistory"]["points"] == []


def test_graphql_rejects_invalid_token_and_hides_data_when_anonymous(processed):
    assert graphql(OVERVIEW_QUERY, "token-invalido").status_code == 401
    assert graphql(OVERVIEW_QUERY).json()["data"]["analytesOverview"] == []
