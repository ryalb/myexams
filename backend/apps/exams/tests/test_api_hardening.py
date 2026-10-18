"""Endurecimento da API: upload, filtros, atomicidade, GraphQL, registro e admin."""

import json

import pytest
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken

from apps.exams import tasks, views
from apps.exams.extraction.fakes import FakeLLMClient
from apps.exams.models import DocumentStatus, ExamDocument, ReviewStatus

from .conftest import SAMPLE_HEADER, create_document, sample_llm_results

pytestmark = pytest.mark.django_db

PDF_BYTES = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF"
DOCS = "/api/v1/exam-documents/"


@pytest.fixture
def api(user):
    client = APIClient()
    client.force_authenticate(user)
    return client


@pytest.fixture
def processed(user, sample_pdf, monkeypatch):
    llm = FakeLLMClient(header=SAMPLE_HEADER, results=sample_llm_results)
    monkeypatch.setattr(tasks, "build_llm_client", lambda: llm)
    document = create_document(user, sample_pdf)
    tasks.process_exam_document(document.pk)
    document.refresh_from_db()
    return document


@pytest.fixture
def enqueued(monkeypatch):
    calls = []

    class Stub:
        @staticmethod
        def delay(*args):
            calls.append(args)

    monkeypatch.setattr(views, "process_exam_document", Stub)
    return calls


def upload(client, content=PDF_BYTES, **extra):
    return client.post(
        DOCS,
        {"file": SimpleUploadedFile("laudo.pdf", content, content_type="application/pdf")},
        format="multipart",
        **extra,
    )


def temp_files(settings):
    directory = settings.EXAMS_UPLOAD_DIR
    return list(directory.glob("*")) if directory.exists() else []


# --- upload -----------------------------------------------------------------


def test_content_length_over_limit_is_rejected_before_reading_body(api, settings, enqueued):
    settings.EXAMS_MAX_UPLOAD_BYTES = 1024
    response = upload(api, CONTENT_LENGTH=str(1024 + 1024 * 1024 + 1))
    assert response.status_code == 413
    assert response.json() == {"code": "file_too_large", "params": {"max_mb": 0}}
    assert not ExamDocument.objects.exists() and not temp_files(settings)


def test_concurrent_identical_upload_becomes_409_and_removes_temp_file(
    api, settings, enqueued, monkeypatch
):
    # Simula a corrida: a checagem prévia não vê o duplicado, o INSERT estoura.
    real_create = ExamDocument.objects.create

    def racing_create(**kwargs):
        real_create(**{**kwargs, "upload_path": ""})
        raise IntegrityError("unique_document_per_user")

    monkeypatch.setattr(ExamDocument.objects, "create", racing_create)

    response = upload(api)
    assert response.status_code == 409
    assert response.json()["code"] == "duplicate_document"
    assert not temp_files(settings)
    assert enqueued == []


def test_unexpected_error_after_save_removes_temp_file(user, settings, monkeypatch):
    def boom(**kwargs):
        raise RuntimeError("db down")

    monkeypatch.setattr(ExamDocument.objects, "create", boom)
    client = APIClient(raise_request_exception=False)
    client.force_authenticate(user)
    assert upload(client).status_code == 500
    assert not temp_files(settings)


def test_enqueue_failure_marks_document_failed_and_removes_file(
    api, settings, monkeypatch, django_capture_on_commit_callbacks
):
    class Broken:
        @staticmethod
        def delay(*args):
            raise ConnectionError("redis down")

    monkeypatch.setattr(views, "process_exam_document", Broken)
    with django_capture_on_commit_callbacks(execute=True):
        response = upload(api)
    assert response.status_code == 202
    document = ExamDocument.objects.get(pk=response.json()["id"])
    assert document.status == DocumentStatus.FAILED
    assert document.error_code == "extraction_failed"
    assert document.upload_path == ""
    assert not temp_files(settings)


# --- filtros: valores inválidos são ignorados --------------------------------


@pytest.mark.parametrize(
    "query",
    [
        "?laboratory=abc",
        "?laboratory=99999999999999999999999",
        "?collected_from=xx",
        "?collected_to=2026-13-45",
        "?status=bogus",
        "?status=",
    ],
)
def test_invalid_document_filters_are_ignored(api, processed, query):
    response = api.get(f"{DOCS}{query}")
    assert response.status_code == 200
    assert response.json()["count"] == 1


@pytest.mark.parametrize(
    "query",
    ["?document=abc", "?review_status=bogus", "?flag=bogus", "?origin=bogus", "?document="],
)
def test_invalid_result_filters_are_ignored(api, processed, query):
    unfiltered = api.get("/api/v1/exam-results/").json()["count"]
    response = api.get(f"/api/v1/exam-results/{query}")
    assert response.status_code == 200
    assert response.json()["count"] == unfiltered


def test_valid_filters_still_apply(api, processed):
    assert api.get(f"{DOCS}?status=failed").json()["count"] == 0
    assert api.get(f"{DOCS}?collected_from=2999-01-01").json()["count"] == 0
    assert api.get("/api/v1/exam-results/?flag=low").json()["count"] >= 1


# --- confirmação, reprocessamento, PATCH ------------------------------------


@pytest.mark.parametrize("state", [DocumentStatus.EXTRACTING, DocumentStatus.UPLOADED])
def test_confirm_requires_a_settled_document(api, processed, state):
    ExamDocument.objects.filter(pk=processed.pk).update(status=state)
    response = api.post(f"{DOCS}{processed.pk}/confirm/")
    assert response.status_code == 409
    assert response.json()["code"] == "document_not_ready"
    processed.refresh_from_db()
    assert processed.status == state
    assert processed.results.filter(review_status=ReviewStatus.NEEDS_REVIEW).exists()


def test_confirm_on_failed_document_is_409(api, processed):
    ExamDocument.objects.filter(pk=processed.pk).update(status=DocumentStatus.FAILED)
    assert api.post(f"{DOCS}{processed.pk}/confirm/").status_code == 409


def test_patch_does_not_flip_status_of_extracting_document(api, processed):
    ExamDocument.objects.filter(pk=processed.pk).update(status=DocumentStatus.EXTRACTING)
    result = processed.results.get(name_raw="Hemoglobina")
    assert (
        api.patch(
            f"/api/v1/exam-results/{result.pk}/", {"value_raw": "12,9"}, format="json"
        ).status_code
        == 200
    )
    processed.refresh_from_db()
    assert processed.status == DocumentStatus.EXTRACTING


def test_reprocess_second_call_is_409(api, processed, enqueued, django_capture_on_commit_callbacks):
    with django_capture_on_commit_callbacks(execute=True):
        first = api.post(f"{DOCS}{processed.pk}/reprocess/")
    assert first.status_code == 202
    processed.refresh_from_db()
    assert processed.status == DocumentStatus.EXTRACTING
    second = api.post(f"{DOCS}{processed.pk}/reprocess/")
    assert second.status_code == 409
    assert second.json()["code"] == "document_not_reprocessable"
    assert enqueued == [(processed.pk, True)]


def test_reprocess_uses_the_upload_throttle_scope(api, processed):
    from rest_framework.throttling import ScopedRateThrottle

    view = views.ExamDocumentViewSet()
    view.action = "reprocess"
    throttles = view.get_throttles()
    assert isinstance(throttles[0], ScopedRateThrottle) and view.throttle_scope == "upload"


def test_patch_is_atomic_when_recompute_fails(api, processed, monkeypatch):
    result = processed.results.get(name_raw="Hemoglobina")
    before = result.value_raw

    def boom(*args, **kwargs):
        raise RuntimeError("recompute failed")

    monkeypatch.setattr(views, "recompute_result", boom)
    client = APIClient(raise_request_exception=False)
    client.force_authenticate(result.user)
    response = client.patch(
        f"/api/v1/exam-results/{result.pk}/", {"value_raw": "12,9"}, format="json"
    )
    assert response.status_code == 500
    result.refresh_from_db()
    assert result.value_raw == before


def test_oversized_patch_fields_are_rejected(api, processed):
    result = processed.results.get(name_raw="Hemoglobina")
    url = f"/api/v1/exam-results/{result.pk}/"
    for field, size in (
        ("value_raw", 256),
        ("unit_raw", 61),
        ("reference_raw", 4001),
        ("notes", 4001),
    ):
        response = api.patch(url, {field: "x" * size}, format="json")
        assert response.status_code == 400, field
        assert field in response.json()


# --- registro e /auth/me ----------------------------------------------------


def test_register_runs_password_validators(settings):
    settings.ALLOW_PUBLIC_REGISTRATION = True
    client = APIClient()
    for weak in ("12345678", "password", "novo@example.com"):
        response = client.post(
            "/api/v1/auth/register/",
            {"email": "novo@example.com", "password": weak},
            format="json",
        )
        assert response.status_code == 400, weak
        assert "password" in response.json()
    assert not get_user_model().objects.filter(email="novo@example.com").exists()


def test_register_rejects_overlong_email(settings):
    settings.ALLOW_PUBLIC_REGISTRATION = True
    email = "a" * 145 + "@x.com"
    response = APIClient().post(
        "/api/v1/auth/register/", {"email": email, "password": "senha-forte-123"}, format="json"
    )
    assert response.status_code == 400
    assert "email" in response.json()


def test_register_accepts_strong_password(settings):
    settings.ALLOW_PUBLIC_REGISTRATION = True
    response = APIClient().post(
        "/api/v1/auth/register/",
        {"email": "novo@example.com", "password": "uma-senha-bem-forte-9"},
        format="json",
    )
    assert response.status_code == 200


# --- GraphQL ----------------------------------------------------------------


def graphql(query, token=None, **extra):
    headers = {"HTTP_AUTHORIZATION": f"Bearer {token}"} if token else {}
    return APIClient().post(
        "/graphql/",
        data=json.dumps({"query": query}),
        content_type="application/json",
        **headers,
        **extra,
    )


def test_graphql_limit_is_clamped(user, sample_pdf, other_user):
    for i in range(3):
        ExamDocument.objects.create(user=user, sha256=f"{i:064d}", original_filename=f"{i}.pdf")
    token = str(AccessToken.for_user(user))
    rows = graphql("{ examDocuments(limit: 1) { id } }", token).json()["data"]["examDocuments"]
    assert len(rows) == 1
    for bad in (0, -5):
        rows = graphql(f"{{ examDocuments(limit: {bad}) {{ id }} }}", token).json()["data"][
            "examDocuments"
        ]
        assert len(rows) == 1
    rows = graphql("{ examDocuments(limit: 100000) { id } }", token).json()["data"]["examDocuments"]
    assert len(rows) == 3


def test_graphql_documents_have_no_n_plus_one(
    user, processed, sample_pdf, django_assert_max_num_queries
):
    for i in range(5):
        ExamDocument.objects.create(user=user, sha256=f"{i:064d}", original_filename=f"{i}.pdf")
    token = str(AccessToken.for_user(user))
    query = "{ examDocuments { id resultCount reviewCount laboratoryName } }"
    with django_assert_max_num_queries(6):
        data = graphql(query, token).json()["data"]["examDocuments"]
    assert len(data) == 6
    by_count = max(data, key=lambda d: d["resultCount"])
    assert by_count["resultCount"] == 11 and by_count["reviewCount"] >= 1

    single = "{ examDocument(id: %d) { resultCount reviewCount } }" % processed.pk
    with django_assert_max_num_queries(6):
        one = graphql(single, token).json()["data"]["examDocument"]
    assert one["resultCount"] == 11


def test_graphiql_is_disabled_when_not_debug(settings):
    assert settings.DEBUG is False
    response = APIClient().get("/graphql/", HTTP_ACCEPT="text/html")
    assert b"graphiql" not in response.content.lower()


def test_graphql_ignores_session_user_when_not_debug(user, processed, client):
    client.force_login(user)
    response = client.post(
        "/graphql/",
        data=json.dumps({"query": "{ analytesOverview { slug } }"}),
        content_type="application/json",
    )
    assert response.json()["data"]["analytesOverview"] == []


def test_graphql_uses_session_user_in_debug(user, processed, client, settings):
    settings.DEBUG = True
    client.force_login(user)
    response = client.post(
        "/graphql/",
        data=json.dumps({"query": "{ analytesOverview { slug } }"}),
        content_type="application/json",
    )
    assert response.json()["data"]["analytesOverview"] != []


# --- admin ------------------------------------------------------------------


def test_admin_does_not_render_phi(client, processed):
    admin_user = get_user_model().objects.create_superuser(
        "admin@example.com", "admin@example.com", "senha-admin-forte-1"
    )
    client.force_login(admin_user)
    processed.page_text = ["SEGREDO-CLINICO-123"]
    processed.save()
    result = processed.results.first()
    result.source_line = "LINHA-SECRETA-456"
    result.raw = {"x": "RAW-SECRETO-789"}
    result.save()

    for url in (
        "/admin/exams/examdocument/",
        f"/admin/exams/examdocument/{processed.pk}/change/",
        "/admin/exams/examresult/",
        f"/admin/exams/examresult/{result.pk}/change/",
    ):
        response = client.get(url)
        assert response.status_code == 200, url
        body = response.content.decode()
        for secret in ("SEGREDO-CLINICO-123", "LINHA-SECRETA-456", "RAW-SECRETO-789"):
            assert secret not in body, url
    assert client.get("/admin/exams/examdocument/add/").status_code == 403
    assert client.get("/admin/exams/examresult/add/").status_code == 403
