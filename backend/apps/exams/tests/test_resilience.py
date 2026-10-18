"""Resiliência por bloco e progresso da extração."""

from collections import Counter
from pathlib import Path

import pytest

from apps.exams import tasks
from apps.exams.codes import ErrorCode, WarningCode
from apps.exams.extraction.blocks import Block
from apps.exams.extraction.errors import ExtractionError
from apps.exams.extraction.fakes import FakeLLMClient
from apps.exams.extraction.pipeline import process_document, unreadable_title
from apps.exams.models import DocumentStatus, ReviewStatus
from apps.exams.serializers import ExamDocumentDetailSerializer, ExamDocumentSerializer

from .conftest import (
    SAMPLE_HEADER,
    build_pdf,
    create_document,
    qualitative_block,
    qualitative_llm_results,
    row,
    sample_llm_results,
)

pytestmark = pytest.mark.django_db

# HEMOGRAMA (LLM) + GLICOSE, CREATININA, TSH, MAGNÉSIO, TESTOSTERONA (regras): uma unidade
# de trabalho cada, porque o hemograma de exemplo cabe numa única chamada.
BLOCK_COUNT = 6


def failing(prefixes: dict[str, ExtractionError], times: int | None = None, base=None):
    """Resultados do fake, mas os blocos cujo texto começa com a chave levantam o erro.

    `times` limita quantas chamadas falham por bloco (None = sempre).
    """
    attempts: Counter = Counter()

    def results(text: str) -> dict:
        key = text.upper()
        for prefix, error in prefixes.items():
            if key.startswith(prefix):
                attempts[prefix] += 1
                if times is None or attempts[prefix] <= times:
                    raise error
        return (base or sample_llm_results)(text)

    return results


def qualitative_pdf(tmp_path, *titles: str):
    """PDF de uma página só com blocos qualitativos: todos vão ao LLM (nenhum sai por regras)."""
    body = [line for title in titles for line in qualitative_block(title, "Negativo")]
    return build_pdf(tmp_path / "qualitativo.pdf", [body])


def run(monkeypatch, user, pdf, results) -> tuple:
    llm = FakeLLMClient(header=SAMPLE_HEADER, results=results)
    monkeypatch.setattr(tasks, "build_llm_client", lambda: llm)
    document = create_document(user, pdf)
    tasks.process_exam_document(document.pk)
    document.refresh_from_db()
    return document, llm


def timeout() -> ExtractionError:
    return ExtractionError(ErrorCode.INFERENCE_TIMEOUT)


def calls_for(llm: FakeLLMClient, prefix: str) -> int:
    return sum(1 for text in llm.result_calls if text.upper().startswith(prefix))


# --- resiliência --------------------------------------------------------------------------


def test_timeout_is_retried_once_and_then_succeeds(monkeypatch, user, sample_pdf):
    document, llm = run(monkeypatch, user, sample_pdf, failing({"HEMOGRAMA": timeout()}, times=1))

    assert calls_for(llm, "HEMOGRAMA") == 2
    assert document.results.filter(name_raw="Hemácias").exists()
    assert WarningCode.BLOCK_UNREADABLE not in {w["code"] for w in document.warnings}
    stats = document.runs.get().stats
    assert stats["skipped_blocks"] == 0
    assert stats["llm_calls"] == 2  # a tentativa que estourou o tempo + a nova tentativa


def test_block_that_keeps_timing_out_is_skipped_with_a_warning(monkeypatch, user, sample_pdf):
    document, llm = run(monkeypatch, user, sample_pdf, failing({"HEMOGRAMA": timeout()}))

    assert calls_for(llm, "HEMOGRAMA") == 2  # a tentativa + uma nova tentativa
    assert not document.results.filter(name_raw="Hemácias").exists()
    assert document.results.filter(name_raw="GLICOSE").exists()  # o resto do laudo foi salvo
    assert document.results.filter(name_raw="TESTOSTERONA LIVRE CALCULADA").exists()
    # Aviso do laudo: página + nome impresso do exame (nunca valores). O hemograma é a página 1.
    assert {
        "code": "block_unreadable",
        "params": {"page": 1, "title": "HEMOGRAMA"},
    } in document.warnings
    assert document.status == DocumentStatus.NEEDS_REVIEW
    run_ = document.runs.get()
    assert run_.status == "success"
    assert run_.stats["skipped_blocks"] == 1
    assert run_.stats["blocks"] == BLOCK_COUNT


def test_partial_read_is_never_presented_as_complete(monkeypatch, user, tmp_path):
    # Todos os resultados lidos são AUTO; só o aviso do laudo manda para revisão.
    body = [
        row("GLICOSE", "", "", "92 mg/dL"),
        [(40, "Material: soro")],
        [(40, "Data da coleta: 25/08/2026 - 07:01:51")],
        [(40, "Valor de Referência: Normal: Glicemia de jejum entre 70 a 99 mg/dL")],
        [(40, "Liberado por: Dr. Teste CRM-CE 1")],
        *qualitative_block("NITRITO", "Negativo"),
    ]
    pdf = build_pdf(tmp_path / "parcial.pdf", [body])

    document, _ = run(monkeypatch, user, pdf, failing({"NITRITO": timeout()}))

    assert [r.review_status for r in document.results.all()] == [ReviewStatus.AUTO]
    assert [w["code"] for w in document.warnings] == [WarningCode.BLOCK_UNREADABLE]
    assert document.status == DocumentStatus.NEEDS_REVIEW


def test_invalid_model_output_skips_the_block_without_pipeline_retry(monkeypatch, user, sample_pdf):
    error = ExtractionError(ErrorCode.INVALID_MODEL_OUTPUT)
    document, llm = run(monkeypatch, user, sample_pdf, failing({"HEMOGRAMA": error}))

    # O cliente já tenta duas vezes internamente; o pipeline não repete.
    assert calls_for(llm, "HEMOGRAMA") == 1
    assert {
        "code": "block_unreadable",
        "params": {"page": 1, "title": "HEMOGRAMA"},
    } in document.warnings
    assert not document.results.filter(name_raw="Hemácias").exists()
    assert document.results.filter(name_raw="CREATININA").exists()  # regras não dependem do LLM
    assert document.status == DocumentStatus.NEEDS_REVIEW


def test_two_unreadable_blocks_on_the_same_page_give_one_warning_each(monkeypatch, user, tmp_path):
    pdf = qualitative_pdf(tmp_path, "NITRITO", "UROBILINOGÊNIO", "BILIRRUBINA")
    prefixes = {"NITRITO": timeout(), "UROBILINOGÊNIO": timeout()}
    document, _ = run(monkeypatch, user, pdf, failing(prefixes, base=qualitative_llm_results))
    unreadable = [w for w in document.warnings if w["code"] == WarningCode.BLOCK_UNREADABLE]
    assert unreadable == [
        {"code": "block_unreadable", "params": {"page": 1, "title": "NITRITO"}},
        {"code": "block_unreadable", "params": {"page": 1, "title": "UROBILINOGÊNIO"}},
    ]
    assert document.runs.get().stats["skipped_blocks"] == 2
    assert document.results.filter(name_raw="BILIRRUBINA").exists()


def test_unreadable_warning_carries_only_page_and_exam_name_never_the_value(
    monkeypatch, user, tmp_path
):
    pdf = qualitative_pdf(tmp_path, "NITRITO", "BILIRRUBINA")
    results = failing({"NITRITO": timeout()}, base=qualitative_llm_results)
    document, _ = run(monkeypatch, user, pdf, results)

    (unreadable,) = (w for w in document.warnings if w["code"] == WarningCode.BLOCK_UNREADABLE)
    assert unreadable["params"] == {"page": 1, "title": "NITRITO"}
    assert "Negativo" not in str(document.warnings)


@pytest.mark.parametrize(
    ("block", "expected"),
    [
        # Título com valor e material: só o nome impresso do exame.
        (Block("NITRITO | Negativo", ["NITRITO | Negativo", "Material: urina"], 1), "NITRITO"),
        (Block("COLESTEROL TOTAL, soro", ["COLESTEROL TOTAL, soro"], 1), "COLESTEROL TOTAL"),
        (Block("CREATININA: 1,20 mg/dL", ["CREATININA: 1,20 mg/dL"], 1), "CREATININA"),
        # Sem título: primeira linha não vazia (também sem o que vem depois de `|`).
        (
            Block("", ["", "  Tempo   de Protrombina | 12,0 s", "Material: soro"], 2),
            "Tempo de Protrombina",
        ),
        # No máximo 80 caracteres.
        (Block("X" * 200, ["X" * 200], 1), "X" * 80),
        (Block("", [], 1), ""),
    ],
)
def test_unreadable_title_is_the_printed_exam_name_only(block, expected):
    assert unreadable_title(block) == expected


@pytest.mark.parametrize(
    "code",
    [
        ErrorCode.INFERENCE_UNAVAILABLE,
        ErrorCode.MODEL_NOT_FOUND,
        ErrorCode.INFERENCE_NOT_LOCAL,
    ],
)
def test_infrastructure_errors_fail_fast(monkeypatch, user, tmp_path, code):
    pdf = qualitative_pdf(tmp_path, "NITRITO", "UROBILINOGÊNIO")
    failures = failing({"NITRITO": ExtractionError(code)}, base=qualitative_llm_results)
    document, llm = run(monkeypatch, user, pdf, failures)

    assert document.status == DocumentStatus.FAILED
    assert document.error_code == code
    assert not document.results.exists()
    assert calls_for(llm, "NITRITO") == 1  # sem nova tentativa
    assert calls_for(llm, "UROBILINOGÊNIO") == 0  # e sem seguir para os demais blocos
    assert document.upload_path == ""


def test_all_blocks_failing_reports_the_real_error_not_no_results(monkeypatch, user, tmp_path):
    def always_timeout(text: str) -> dict:
        raise timeout()

    pdf = qualitative_pdf(tmp_path, "NITRITO", "UROBILINOGÊNIO")
    document, _ = run(monkeypatch, user, pdf, always_timeout)

    assert document.status == DocumentStatus.FAILED
    assert document.error_code == ErrorCode.INFERENCE_TIMEOUT
    assert not document.results.exists()


def test_no_results_without_failures_is_still_no_results_found(monkeypatch, user, tmp_path):
    pdf = qualitative_pdf(tmp_path, "NITRITO")
    document, _ = run(monkeypatch, user, pdf, lambda text: {"results": []})
    assert document.error_code == ErrorCode.NO_RESULTS_FOUND


def test_extra_keys_from_the_model_are_ignored(monkeypatch, user, sample_pdf):
    def chatty(text: str) -> dict:
        output = sample_llm_results(text)
        for item in output["results"]:
            item.update(
                section="INVENTADA",
                method="Inventado",
                specimen="urina",
                collected_at="01/01/2000",
                performing_lab="Outro Lab",
                note="obs",
                source_line="linha inventada",
            )
        return {**output, "notes": ["qualquer coisa"]}

    document, _ = run(monkeypatch, user, sample_pdf, chatty)

    red_cells = document.results.get(name_raw="Hemácias", origin="current")
    assert (red_cells.method, red_cells.specimen) == ("Automatizado", "sangue")
    assert red_cells.section_raw == "ERITROGRAMA"
    # Uma linha de origem que não existe no bloco é descartada e derivada em código.
    assert red_cells.source_line == "Hemácias | 4,85 | milhões/uL | 4,50 a 6,50 milhões/uL"
    assert red_cells.collected_at.year == 2026
    assert red_cells.notes == ""


# --- progresso ----------------------------------------------------------------------------


def test_processed_document_ends_with_full_progress(monkeypatch, user, sample_pdf):
    llm = FakeLLMClient(header=SAMPLE_HEADER, results=sample_llm_results)
    monkeypatch.setattr(tasks, "build_llm_client", lambda: llm)
    document = create_document(user, sample_pdf)
    assert (document.progress_done, document.progress_total) == (0, 0)

    tasks.process_exam_document(document.pk)
    document.refresh_from_db()

    assert document.progress_total == BLOCK_COUNT
    assert document.progress_done == BLOCK_COUNT


def test_progress_callback_is_monotonic_and_counts_every_block(monkeypatch, user, sample_pdf):
    llm = FakeLLMClient(header=SAMPLE_HEADER, results=failing({"HEMOGRAMA": timeout()}))
    monkeypatch.setattr(tasks, "build_llm_client", lambda: llm)
    document = create_document(user, sample_pdf)
    tasks.process_exam_document(document.pk)  # grava o texto das páginas
    document.refresh_from_db()

    calls: list[tuple[int, int]] = []
    process_document(document, llm, on_progress=lambda done, total: calls.append((done, total)))

    # (0, total) após a segmentação e um aviso por unidade de trabalho, lida ou pulada.
    assert calls == [(i, BLOCK_COUNT) for i in range(BLOCK_COUNT + 1)]
    document.refresh_from_db()
    assert document.progress_done == document.progress_total == BLOCK_COUNT


def test_task_broadcasts_extracting_after_every_block(monkeypatch, user, sample_pdf):
    events: list[tuple[int, str]] = []
    monkeypatch.setattr(
        tasks,
        "broadcast_document_event",
        lambda user_id, document_id, status: events.append((document_id, status)),
    )
    llm = FakeLLMClient(header=SAMPLE_HEADER, results=sample_llm_results)
    monkeypatch.setattr(tasks, "build_llm_client", lambda: llm)
    document = create_document(user, sample_pdf)
    tasks.process_exam_document(document.pk)

    statuses = [status for _, status in events]
    # 1 (início) + 1 (0/total) + 1 por bloco, e o status final.
    assert statuses.count("extracting") == 2 + BLOCK_COUNT
    assert statuses[-1] == DocumentStatus.NEEDS_REVIEW


def test_progress_is_written_while_extracting(monkeypatch, user, sample_pdf):
    seen: list[tuple[int, int]] = []

    def spy(text: str) -> dict:
        from apps.exams.models import ExamDocument

        d = ExamDocument.objects.get(pk=document.pk)
        seen.append((d.progress_done, d.progress_total))
        return sample_llm_results(text)

    llm = FakeLLMClient(header=SAMPLE_HEADER, results=spy)
    monkeypatch.setattr(tasks, "build_llm_client", lambda: llm)
    document = create_document(user, sample_pdf)
    tasks.process_exam_document(document.pk)

    # Só o hemograma chega ao LLM; as regras rodam primeiro, então 5 dos 6 blocos já estão prontos.
    assert seen == [(BLOCK_COUNT - 1, BLOCK_COUNT)]


def test_reprocess_resets_progress_at_start(monkeypatch, user, sample_pdf):
    llm = FakeLLMClient(header=SAMPLE_HEADER, results=sample_llm_results)
    monkeypatch.setattr(tasks, "build_llm_client", lambda: llm)
    document = create_document(user, sample_pdf)
    tasks.process_exam_document(document.pk)

    seen: list[tuple[int, int]] = []
    original = llm.results

    def spy(text: str) -> dict:
        from apps.exams.models import ExamDocument

        d = ExamDocument.objects.get(pk=document.pk)
        seen.append((d.progress_done, d.progress_total))
        return original(text)

    llm.results = spy
    tasks.process_exam_document(document.pk, True)
    # Recomeçou do zero (não de 6/6): quando o LLM é chamado, só os 5 blocos de regra estão prontos.
    assert seen[0] == (BLOCK_COUNT - 1, BLOCK_COUNT)


def test_progress_fields_are_exposed_read_only(user, sample_pdf):
    document = create_document(user, sample_pdf)
    for serializer in (ExamDocumentSerializer, ExamDocumentDetailSerializer):
        data = serializer(document).data
        assert data["progress_done"] == 0
        assert data["progress_total"] == 0
        assert {"progress_done", "progress_total"} <= set(serializer.Meta.read_only_fields)


def test_progress_in_api(monkeypatch, user, sample_pdf):
    from rest_framework.test import APIClient

    llm = FakeLLMClient(header=SAMPLE_HEADER, results=sample_llm_results)
    monkeypatch.setattr(tasks, "build_llm_client", lambda: llm)
    document = create_document(user, sample_pdf)
    tasks.process_exam_document(document.pk)

    client = APIClient()
    client.force_authenticate(user)
    listing = client.get("/api/v1/exam-documents/").json()["results"][0]
    detail = client.get(f"/api/v1/exam-documents/{document.pk}/").json()
    for body in (listing, detail):
        assert (body["progress_done"], body["progress_total"]) == (BLOCK_COUNT, BLOCK_COUNT)


def test_stored_pdf_is_deleted_even_when_blocks_fail(monkeypatch, user, sample_pdf):
    document = create_document(user, sample_pdf)
    stored = Path(document.upload_path)
    llm = FakeLLMClient(header=SAMPLE_HEADER, results=failing({"HEMOGRAMA": timeout()}))
    monkeypatch.setattr(tasks, "build_llm_client", lambda: llm)
    tasks.process_exam_document(document.pk)
    assert not stored.exists()


@pytest.mark.parametrize(
    ("document_name", "user_name", "expected"),
    [
        ("MARIA TESTE SOUZA.", "Maria Teste Souza", True),  # ponto final do laudo
        ("MARIA TESTE SOUZA", "MARIA TESTE SOUZA", True),
        ("José da Silva", "Jose Silva", True),  # acento e caixa
        ("Joana Pereira", "Maria Teste", False),
        ("MARIA TESTE SOUZA.", "", True),  # sem nome cadastrado: nada a comparar
        ("", "Maria", True),
    ],
)
def test_names_match_ignores_punctuation_accents_and_case(document_name, user_name, expected):
    from apps.exams.extraction.validate import names_match

    assert names_match(document_name, user_name) is expected


def test_header_regex_skips_client_code_and_reads_the_patient_name():
    from apps.exams.extraction.pipeline import parse_header

    text = (
        "Cód. Cliente: 100200 | Cadastro: UNIDADE CENTRO\n"
        "Cliente: MARIA TESTE SOUZA. | O.S: 021-11111-222\n"
        "Data Nasc.: 05/03/1977 | Atendimento: 25/08/2026 - 06:44:05\n"
        "CPF: [CPF] | Solicitante: DR EXEMPLO TESTE\n"
    )
    header = parse_header({}, text)
    assert header.patient_name == "MARIA TESTE SOUZA."
    assert header.accession_number == "021-11111-222"
    assert header.ordering_physician == "DR EXEMPLO TESTE"
