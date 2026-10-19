"""Modo inline da nuvem (`CELERY_TASK_ALWAYS_EAGER=true`): o laudo é lido na própria requisição,
sem broker, sem Redis e sem WebSocket. Dados sintéticos.
"""

from datetime import timedelta

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from apps.exams import views
from apps.exams.models import DocumentStatus, ExamDocument

from .conftest import SPARSE_HEADER, build_pdf
from .test_layouts_table import TABLE_BODY

pytestmark = pytest.mark.django_db


@pytest.fixture
def api(user):
    client = APIClient()
    client.force_authenticate(user)
    return client


@pytest.fixture
def autocommit_request(monkeypatch):
    """Uma requisição real roda em autocommit: o `on_commit` dispara na hora. Dentro da transação do
    teste ele ficaria para depois da resposta."""
    monkeypatch.setattr(views.transaction, "on_commit", lambda func: func())


@pytest.fixture
def no_broker_or_redis(monkeypatch):
    """Qualquer tentativa de abrir conexão com broker (kombu) ou Redis derruba o teste."""
    import kombu.connection
    import redis.connection

    def forbidden(*args, **kwargs):
        raise AssertionError("o modo inline não pode abrir conexão com broker nem com Redis")

    monkeypatch.setattr(kombu.connection.Connection, "connect", forbidden)
    monkeypatch.setattr(kombu.connection.Connection, "ensure_connection", forbidden)
    monkeypatch.setattr(redis.connection.AbstractConnection, "connect", forbidden)


@pytest.fixture
def pdf_bytes(tmp_path):
    return build_pdf(tmp_path / "inline.pdf", [TABLE_BODY], SPARSE_HEADER).read_bytes()


def upload(client, content):
    from django.core.files.uploadedfile import SimpleUploadedFile

    return client.post(
        "/api/v1/exam-documents/",
        {"file": SimpleUploadedFile("laudo.pdf", content, content_type="application/pdf")},
        format="multipart",
    )


def test_the_guard_fixture_really_blocks_broker_and_redis(no_broker_or_redis):
    import kombu

    with pytest.raises(AssertionError, match="modo inline"):
        kombu.Connection("redis://localhost:6380/0").connect()


def test_upload_is_processed_inside_the_request_and_the_response_says_so(
    api, settings, autocommit_request, no_broker_or_redis, pdf_bytes
):
    settings.EXTRACTION_USE_LLM = False
    settings.CELERY_TASK_ALWAYS_EAGER = True

    response = upload(api, pdf_bytes)

    assert response.status_code == 202
    body = response.json()
    assert body["status"] in {DocumentStatus.COMPLETED, DocumentStatus.NEEDS_REVIEW}
    assert body["result_count"] > 0
    document = ExamDocument.objects.get(pk=body["id"])
    assert document.status == body["status"] and document.upload_path == ""
    assert not list(settings.EXAMS_UPLOAD_DIR.glob("*.pdf"))  # o PDF foi apagado ao fim


def test_inline_mode_works_with_no_channel_layer_at_all(
    api, settings, autocommit_request, no_broker_or_redis, pdf_bytes
):
    settings.EXTRACTION_USE_LLM = False
    settings.CHANNEL_LAYERS = {}

    assert upload(api, pdf_bytes).json()["status"] != DocumentStatus.UPLOADED


def test_an_unreachable_redis_channel_layer_never_breaks_the_extraction(
    api, settings, autocommit_request, pdf_bytes
):
    settings.EXTRACTION_USE_LLM = False
    settings.CHANNEL_LAYERS = {
        "default": {
            "BACKEND": "channels_redis.core.RedisChannelLayer",
            "CONFIG": {"hosts": ["redis://127.0.0.1:1/0"]},
        }
    }

    assert upload(api, pdf_bytes).json()["status"] != DocumentStatus.UPLOADED


def test_without_inline_mode_the_response_keeps_the_queued_state(
    api, settings, monkeypatch, pdf_bytes
):
    settings.CELERY_TASK_ALWAYS_EAGER = False
    calls = []

    class Stub:
        @staticmethod
        def delay(*args):
            calls.append(args)

    monkeypatch.setattr(views, "process_exam_document", Stub)
    monkeypatch.setattr(views.transaction, "on_commit", lambda func: func())

    response = upload(api, pdf_bytes)

    assert response.json()["status"] == DocumentStatus.UPLOADED
    assert len(calls) == 1


def test_inline_upload_sweeps_documents_a_cut_off_function_left_extracting(
    api, user, settings, autocommit_request, pdf_bytes
):
    settings.EXTRACTION_USE_LLM = False
    settings.EXAMS_ORPHAN_HOURS = 0.1
    stuck = ExamDocument.objects.create(
        user=user, sha256="a" * 64, status=DocumentStatus.EXTRACTING, original_filename="x.pdf"
    )
    ExamDocument.objects.filter(pk=stuck.pk).update(updated_at=timezone.now() - timedelta(hours=1))

    upload(api, pdf_bytes)

    stuck.refresh_from_db()
    assert stuck.status == DocumentStatus.FAILED and stuck.error_code == "extraction_failed"


def test_a_failing_sweep_never_blocks_the_upload(
    api, settings, autocommit_request, monkeypatch, pdf_bytes
):
    settings.EXTRACTION_USE_LLM = False

    def boom():
        raise RuntimeError("falha simulada")

    monkeypatch.setattr(views, "sweep_orphans", boom)

    assert upload(api, pdf_bytes).status_code == 202


def test_me_endpoint_exposes_the_upload_limit(api, settings):
    settings.EXAMS_MAX_UPLOAD_BYTES = 4 * 1024 * 1024

    assert api.get("/api/v1/auth/me/").json()["limits"] == {"max_upload_mb": 4}
