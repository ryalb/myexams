"""`EXTRACTION_USE_LLM=false`: o worker lê só com regras e nem tenta falar com o Ollama."""

import pytest

from apps.exams import tasks
from apps.exams.codes import ErrorCode, WarningCode
from apps.exams.extraction import llm as llm_module
from apps.exams.extraction.llm import DisabledLLMClient, OllamaClient, build_llm_client
from apps.exams.models import DocumentStatus

from .conftest import SPARSE_HEADER, build_pdf, create_document, qualitative_block
from .test_layouts_table import TABLE_BODY


@pytest.fixture
def ollama_is_forbidden(monkeypatch):
    """Qualquer tentativa de montar o cliente do Ollama ou de abrir uma conexão derruba o teste."""

    def forbidden(*args, **kwargs):
        raise AssertionError("o Ollama não deveria ser usado com EXTRACTION_USE_LLM=false")

    monkeypatch.setattr(llm_module, "OllamaClient", forbidden)
    monkeypatch.setattr(llm_module.requests, "post", forbidden)
    monkeypatch.setattr(llm_module.requests, "Session", forbidden)


def run_worker(user, pdf):
    """Roda a tarefa do worker de verdade (sem trocar `build_llm_client`)."""
    document = create_document(user, pdf)
    tasks.process_exam_document(document.pk)
    document.refresh_from_db()
    return document, document.runs.get()


# --- o cliente ---------------------------------------------------------------------------


def test_build_llm_client_follows_the_setting(settings):
    settings.EXTRACTION_USE_LLM = False
    assert isinstance(build_llm_client(), DisabledLLMClient)

    settings.EXTRACTION_USE_LLM = True
    assert isinstance(build_llm_client(), OllamaClient)


def test_disabled_client_answers_nothing_and_has_no_model_names():
    client = DisabledLLMClient()

    assert client.model == "" and client.vision_model == ""
    assert client.extract_header("texto") == {}
    assert client.extract_results("bloco", 1) == {}
    assert client.match_analyte("nome", "mg/dL", [("a", "A")]) == ""
    assert client.transcribe_page(b"png") == ""


# --- o worker ----------------------------------------------------------------------------


@pytest.mark.django_db
def test_worker_reads_with_rules_only_and_lists_what_it_could_not_read(
    user, tmp_path, settings, ollama_is_forbidden
):
    settings.EXTRACTION_USE_LLM = False
    pdf = build_pdf(
        tmp_path / "regras.pdf",
        [TABLE_BODY, qualitative_block("NITRITO", "Negativo")],
        SPARSE_HEADER,
    )

    document, run = run_worker(user, pdf)

    assert document.status == DocumentStatus.NEEDS_REVIEW
    assert document.results.filter(name_raw="Glicose").exists()  # o que as regras leem entra
    codes = [w["code"] for w in document.warnings]
    assert WarningCode.BLOCK_UNREADABLE in codes
    unread = next(w for w in document.warnings if w["code"] == WarningCode.BLOCK_UNREADABLE)
    assert unread["params"]["title"] == "NITRITO"
    # desligado de propósito não é "tempo esgotado"
    assert WarningCode.LLM_BUDGET_EXCEEDED not in codes
    assert run.model == "" and run.vision_model == ""
    assert run.stats["llm_enabled"] is False
    assert run.stats["llm_calls"] == 0
    assert run.stats["header_by_llm"] is False


@pytest.mark.django_db
def test_enabled_setting_still_reports_the_model_in_the_run_stats(
    monkeypatch, user, tmp_path, settings
):
    from apps.exams.extraction.fakes import FakeLLMClient

    from .conftest import SAMPLE_HEADER, qualitative_llm_results

    settings.EXTRACTION_USE_LLM = True
    monkeypatch.setattr(
        tasks,
        "build_llm_client",
        lambda: FakeLLMClient(header=SAMPLE_HEADER, results=qualitative_llm_results),
    )
    pdf = build_pdf(tmp_path / "com-modelo.pdf", [qualitative_block("NITRITO", "Negativo")])

    document, run = run_worker(user, pdf)

    assert run.stats["llm_enabled"] is True
    assert run.stats["llm_calls"] >= 1
    assert document.results.filter(name_raw__iexact="NITRITO").exists()


@pytest.mark.django_db
def test_a_pdf_that_is_only_images_fails_with_a_clear_code(
    user, tmp_path, settings, ollama_is_forbidden
):
    settings.EXTRACTION_USE_LLM = False
    pdf = build_pdf(tmp_path / "escaneado.pdf", [None, None])  # páginas sem nenhum texto

    document, _ = run_worker(user, pdf)

    assert document.status == DocumentStatus.FAILED
    assert document.error_code == ErrorCode.LLM_DISABLED
    assert not document.results.exists()


@pytest.mark.django_db
def test_a_blank_page_in_a_text_report_becomes_an_unread_page_not_a_failure(
    user, tmp_path, settings, ollama_is_forbidden
):
    settings.EXTRACTION_USE_LLM = False
    pdf = build_pdf(tmp_path / "misto.pdf", [TABLE_BODY, None], SPARSE_HEADER)

    document, _ = run_worker(user, pdf)

    assert document.status == DocumentStatus.NEEDS_REVIEW
    assert document.results.filter(name_raw="Glicose").exists()
    pages = [
        w["params"]
        for w in document.warnings
        if w["code"] == WarningCode.BLOCK_UNREADABLE and w["params"].get("page") == 2
    ]
    assert pages and "title" not in pages[0]
