"""Telemetria por chamada ao LLM: medidas do Ollama, estatísticas da execução e privacidade.

Sem rede e sem Ollama: sessões HTTP stub (como em `test_llm_stream`) e LLMs fake.
"""

import json

import pytest
import requests
from rest_framework.test import APIClient

from apps.exams import tasks
from apps.exams.codes import ErrorCode
from apps.exams.extraction import pipeline
from apps.exams.extraction.errors import ExtractionError
from apps.exams.extraction.fakes import FakeLLMClient
from apps.exams.extraction.llm import CallMetrics, OllamaClient
from apps.exams.extraction.telemetry import MAX_CALL_DETAILS, LLMTelemetry
from apps.exams.models import RunStatus

from .conftest import (
    PAGE_1,
    SAMPLE_HEADER,
    build_pdf,
    create_document,
    qualitative_block,
    qualitative_llm_results,
    sample_llm_results,
)
from .test_llm_stream import StubResponse, StubSession, chunk, ndjson

DETAIL_KEYS = {
    "kind",
    "page",
    "seconds",
    "load_seconds",
    "prompt_tokens",
    "output_tokens",
    "tokens_per_second",
    "outcome",
}


# --- cliente: medidas do pedaço final do Ollama -------------------------------------------


def final_chunk(**extra) -> dict:
    return {**chunk("", done=True), **extra}


def client_with(response=None, error=None, **kwargs) -> OllamaClient:
    client = OllamaClient(**kwargs)
    client._session = StubSession(response, error)
    return client


def test_final_chunk_becomes_call_metrics(monkeypatch):
    from apps.exams.extraction import llm as llm_module

    now = iter([100.0, 107.5])
    monkeypatch.setattr(llm_module, "_now", lambda: next(now, 107.5))
    response = StubResponse(
        ndjson(
            chunk("x"),
            final_chunk(
                total_duration=9_000_000_000,
                load_duration=2_500_000_000,
                prompt_eval_count=420,
                prompt_eval_duration=1_000_000_000,
                eval_count=60,
                eval_duration=4_000_000_000,
            ),
        )
    )
    client = client_with(response)

    client.extract_header("texto")

    (metrics,) = client.drain_metrics()
    assert metrics == CallMetrics(
        kind="header",
        seconds=7.5,
        load_seconds=2.5,
        prompt_tokens=420,
        output_tokens=60,
        eval_seconds=4.0,
        outcome="ok",
    )


def test_final_chunk_without_measures_gives_null_fields():
    client = client_with(StubResponse(ndjson(final_chunk())))
    client.extract_results("linha", 1)
    (metrics,) = client.drain_metrics()
    assert metrics.kind == "results" and metrics.outcome == "ok"
    assert metrics.load_seconds is None
    assert metrics.prompt_tokens is None and metrics.output_tokens is None
    assert metrics.eval_seconds is None


def test_garbage_measures_are_ignored():
    response = StubResponse(
        ndjson(final_chunk(eval_count="muitos", eval_duration=-5, load_duration=True))
    )
    client = client_with(response)
    client.extract_results("linha", 1)
    (metrics,) = client.drain_metrics()
    assert metrics.output_tokens is None
    assert metrics.eval_seconds is None
    assert metrics.load_seconds is None


def test_each_entry_point_labels_its_kind():
    client = OllamaClient()
    responses = [
        StubResponse(ndjson(final_chunk(eval_count=1))),  # header
        StubResponse(ndjson(final_chunk(eval_count=2))),  # results
        StubResponse(ndjson(chunk('{"slug": ""}', done=True))),  # match
        StubResponse(ndjson(final_chunk(eval_count=4))),  # vision
    ]
    session = StubSession()
    session.post = lambda url, **kw: responses.pop(0)
    client._session = session

    client.extract_header("t")
    client.extract_results("t", 1)
    client.match_analyte("a", "b", [("x", "X")])
    client.transcribe_page(b"png")

    assert [m.kind for m in client.drain_metrics()] == ["header", "results", "match", "vision"]


def test_drain_metrics_clears_the_list():
    client = client_with(StubResponse(ndjson(final_chunk(eval_count=3))))
    client.extract_results("linha", 1)
    assert len(client.drain_metrics()) == 1
    assert client.drain_metrics() == []
    assert client.metrics == []


def test_stall_timeout_is_recorded_as_timeout_without_tokens():
    client = client_with(error=requests.ReadTimeout("silêncio"))
    with pytest.raises(ExtractionError):
        client.extract_results("linha", 1)
    (metrics,) = client.drain_metrics()
    assert metrics.outcome == "timeout" and metrics.kind == "results"
    assert metrics.output_tokens is None and metrics.load_seconds is None


def test_wall_clock_cap_is_recorded_as_timeout(monkeypatch):
    from apps.exams.extraction import llm as llm_module

    now = [1000.0]
    monkeypatch.setattr(llm_module, "_now", lambda: now[0])

    class Ticking(StubResponse):
        def iter_lines(self, chunk_size=None):
            for line in self._lines:
                now[0] += 100
                yield line

    client = client_with(Ticking(ndjson(*[chunk("x") for _ in range(10)])), timeout=250)
    with pytest.raises(ExtractionError) as info:
        client.extract_results("linha", 1)
    assert info.value.code == ErrorCode.INFERENCE_TIMEOUT
    (metrics,) = client.drain_metrics()
    assert metrics.outcome == "timeout"
    assert metrics.seconds >= 250


def test_error_line_is_recorded_as_error():
    client = client_with(StubResponse(ndjson({"error": "falha interna"})))
    with pytest.raises(ExtractionError):
        client.extract_results("linha", 1)
    (metrics,) = client.drain_metrics()
    assert metrics.outcome == "error"
    assert metrics.output_tokens is None


def test_connection_refused_and_http_error_are_recorded_as_error():
    refused = client_with(error=requests.ConnectionError("recusada"))
    with pytest.raises(ExtractionError):
        refused.extract_header("t")
    assert [m.outcome for m in refused.drain_metrics()] == ["error"]

    http = client_with(StubResponse([], status_code=500))
    with pytest.raises(ExtractionError):
        http.extract_header("t")
    assert [m.outcome for m in http.drain_metrics()] == ["error"]


def test_metrics_never_carry_prompt_or_output_text():
    response = StubResponse(ndjson(chunk("Hemácias|4,85"), final_chunk(eval_count=5)))
    client = client_with(response)
    client.extract_results("Hemácias | 4,85", 1)
    (metrics,) = client.drain_metrics()
    assert "Hem" not in repr(metrics) and "4,85" not in repr(metrics)


# --- LLMTelemetry --------------------------------------------------------------------------


def test_details_are_capped_dropping_the_oldest_but_totals_count_everything():
    telemetry = LLMTelemetry()
    total = MAX_CALL_DETAILS + 50
    for number in range(total):
        telemetry.record(
            CallMetrics("results", seconds=number, output_tokens=10, eval_seconds=1.0), page=1
        )

    stats = telemetry.stats()
    details = stats["llm_calls_detail"]
    assert len(details) == MAX_CALL_DETAILS == 200
    assert details[0]["seconds"] == 50.0 and details[-1]["seconds"] == total - 1
    assert stats["llm_output_tokens"] == 10 * total
    assert stats["llm_avg_tokens_per_second"] == 10.0


def test_floats_are_rounded_to_two_decimals_and_rate_comes_from_eval_time():
    telemetry = LLMTelemetry()
    telemetry.record(
        CallMetrics(
            "results", seconds=1.23456, load_seconds=0.98765, output_tokens=100, eval_seconds=3.0
        ),
        page=2,
    )
    (detail,) = telemetry.stats()["llm_calls_detail"]
    assert detail == {
        "kind": "results",
        "page": 2,
        "seconds": 1.23,
        "load_seconds": 0.99,
        "prompt_tokens": None,
        "output_tokens": 100,
        "tokens_per_second": 33.33,
        "outcome": "ok",
    }


def test_zero_eval_time_gives_null_rate():
    telemetry = LLMTelemetry()
    telemetry.record(CallMetrics("results", seconds=1, output_tokens=7, eval_seconds=0.0), None)
    stats = telemetry.stats()
    assert stats["llm_calls_detail"][0]["tokens_per_second"] is None
    assert stats["llm_avg_tokens_per_second"] is None
    assert stats["llm_output_tokens"] == 7


# --- pipeline ------------------------------------------------------------------------------


class MeteredLLM(FakeLLMClient):
    """Fake que, como o cliente real, deixa medidas para o `drain_metrics` a cada chamada."""

    def __init__(self, per_call: list[CallMetrics] | None = None, **kwargs) -> None:
        super().__init__(**kwargs)
        self._per_call = list(per_call or [])

    def _measure(self) -> None:
        if self._per_call:
            self.fake_metrics.append(self._per_call.pop(0))

    def extract_results(self, block_text: str, page: int) -> dict:
        self._measure()
        return super().extract_results(block_text, page)

    def transcribe_page(self, png: bytes) -> str:
        self._measure()
        return super().transcribe_page(png)


def qualitative_pdf(tmp_path, *titles: str, name: str = "qualitativo.pdf"):
    body = [line for title in titles for line in qualitative_block(title, "Negativo")]
    return build_pdf(tmp_path / name, [body])


def run_task(monkeypatch, user, pdf, llm):
    monkeypatch.setattr(tasks, "build_llm_client", lambda: llm)
    document = create_document(user, pdf)
    tasks.process_exam_document(document.pk)
    document.refresh_from_db()
    return document, document.runs.latest("started_at")


@pytest.mark.django_db
def test_plain_fake_gets_wall_time_only_and_one_detail_per_call(monkeypatch, user, tmp_path):
    class Clock:
        now = 1000.0

        def __call__(self):
            return self.now

    clock = Clock()
    monkeypatch.setattr(pipeline, "_clock", clock)

    def results(text: str) -> dict:
        clock.now += 2.5
        return qualitative_llm_results(text)

    llm = FakeLLMClient(header=SAMPLE_HEADER, results=results)
    pdf = qualitative_pdf(tmp_path, "NITRITO", "UROBILINOGÊNIO", "BILIRRUBINA")
    _, run = run_task(monkeypatch, user, pdf, llm)

    stats = run.stats
    everything = stats["llm_calls_detail"]
    details = [d for d in everything if d["kind"] == "results"]
    assert run.status == RunStatus.SUCCESS
    assert len(details) == stats["llm_calls"] == 3
    # Os demais são as escolhas de exame pelo modelo (sem página), também medidas.
    assert {d["kind"] for d in everything} <= {"results", "match"}
    assert all(d["page"] is None for d in everything if d["kind"] == "match")
    for detail in details:
        assert detail == {
            "kind": "results",
            "page": 1,
            "seconds": 2.5,
            "load_seconds": None,
            "prompt_tokens": None,
            "output_tokens": None,
            "tokens_per_second": None,
            "outcome": "ok",
        }
    assert stats["llm_output_tokens"] == 0
    assert stats["llm_avg_tokens_per_second"] is None
    assert stats["llm_load_seconds"] == 0
    assert stats["llm_seconds"] == 7.5


@pytest.mark.django_db
def test_totals_are_computed_from_the_sums_not_the_mean_of_means(monkeypatch, user, tmp_path):
    per_call = [
        CallMetrics(
            "results",
            seconds=5.0,
            load_seconds=3.0,
            prompt_tokens=300,
            output_tokens=100,
            eval_seconds=1.0,
        ),
        CallMetrics(
            "results",
            seconds=12.0,
            load_seconds=0.0,
            prompt_tokens=310,
            output_tokens=100,
            eval_seconds=9.0,
        ),
        CallMetrics("results", seconds=1.0),  # sem tokens: fora da taxa
    ]
    llm = MeteredLLM(per_call, header=SAMPLE_HEADER, results=qualitative_llm_results)
    pdf = qualitative_pdf(tmp_path, "NITRITO", "UROBILINOGÊNIO", "BILIRRUBINA")
    _, run = run_task(monkeypatch, user, pdf, llm)

    stats = run.stats
    details = [d for d in stats["llm_calls_detail"] if d["kind"] == "results"]
    assert [d["page"] for d in details] == [1, 1, 1]
    assert [d["tokens_per_second"] for d in details] == [100.0, 11.11, None]
    assert [d["seconds"] for d in details] == [5.0, 12.0, 1.0]
    assert [d["prompt_tokens"] for d in details] == [300, 310, None]
    assert stats["llm_output_tokens"] == 200
    assert stats["llm_avg_tokens_per_second"] == 20.0  # 200 tokens / 10 s (média das médias: 55,6)
    assert stats["llm_load_seconds"] == 3.0


@pytest.mark.django_db
def test_failed_run_keeps_the_calls_measured_so_far(monkeypatch, user, tmp_path):
    calls = {"count": 0}

    def results(text: str) -> dict:
        calls["count"] += 1
        if calls["count"] == 1:
            return {"results": []}  # vazio: o pipeline pergunta de novo
        raise ExtractionError(ErrorCode.INFERENCE_TIMEOUT)

    per_call = [
        CallMetrics("results", seconds=4.0, output_tokens=0, eval_seconds=0.5),
        CallMetrics("results", seconds=120.0, outcome="timeout"),
        CallMetrics("results", seconds=120.0, outcome="timeout"),
    ]
    llm = MeteredLLM(per_call, header=SAMPLE_HEADER, results=results)
    document, run = run_task(monkeypatch, user, qualitative_pdf(tmp_path, "NITRITO"), llm)

    assert document.status == "failed"
    assert run.status == RunStatus.FAILED
    assert run.error_code == ErrorCode.INFERENCE_TIMEOUT
    assert [d["outcome"] for d in run.stats["llm_calls_detail"]] == ["ok", "timeout", "timeout"]
    assert [d["seconds"] for d in run.stats["llm_calls_detail"]] == [4.0, 120.0, 120.0]
    assert run.stats["llm_avg_tokens_per_second"] == 0.0  # 0 token em 0,5 s
    assert set(run.stats) == {
        "llm_calls_detail",
        "llm_output_tokens",
        "llm_avg_tokens_per_second",
        "llm_load_seconds",
    }


@pytest.mark.django_db
def test_failed_run_with_a_plain_fake_records_timeout_outcome(monkeypatch, user, tmp_path):
    def results(text: str) -> dict:
        raise ExtractionError(ErrorCode.INFERENCE_TIMEOUT)

    llm = FakeLLMClient(header=SAMPLE_HEADER, results=results)
    _, run = run_task(monkeypatch, user, qualitative_pdf(tmp_path, "NITRITO"), llm)

    assert run.status == RunStatus.FAILED
    outcomes = [d["outcome"] for d in run.stats["llm_calls_detail"]]
    assert outcomes and set(outcomes) == {"timeout"}
    assert all(d["tokens_per_second"] is None for d in run.stats["llm_calls_detail"])


@pytest.mark.django_db
def test_other_failures_are_recorded_as_error(monkeypatch, user, tmp_path):
    def results(text: str) -> dict:
        raise ExtractionError(ErrorCode.INFERENCE_UNAVAILABLE)

    llm = FakeLLMClient(header=SAMPLE_HEADER, results=results)
    _, run = run_task(monkeypatch, user, qualitative_pdf(tmp_path, "NITRITO"), llm)

    assert run.error_code == ErrorCode.INFERENCE_UNAVAILABLE
    assert [d["outcome"] for d in run.stats["llm_calls_detail"]] == ["error"]


@pytest.mark.django_db
def test_vision_calls_are_recorded_with_their_page(monkeypatch, user, tmp_path):
    # Página 1 com texto; página 2 em branco, lida pelo modelo de visão.
    pdf = build_pdf(tmp_path / "misto.pdf", [PAGE_1, None])
    per_call = [
        CallMetrics("results", seconds=3.0, output_tokens=50, eval_seconds=10.0),
        CallMetrics("vision", seconds=40.0, load_seconds=12.0, output_tokens=80, eval_seconds=16.0),
    ]
    llm = MeteredLLM(per_call, header=SAMPLE_HEADER, results=sample_llm_results)
    # A visão roda no ingest, antes dos resultados: a ordem das medidas acompanha as chamadas.
    llm._per_call.reverse()
    _, run = run_task(monkeypatch, user, pdf, llm)

    by_kind = {d["kind"]: d for d in run.stats["llm_calls_detail"]}
    assert by_kind["vision"]["page"] == 2
    assert by_kind["vision"]["load_seconds"] == 12.0
    assert by_kind["vision"]["tokens_per_second"] == 5.0
    assert by_kind["results"]["page"] == 1
    assert run.stats["llm_load_seconds"] == 12.0


@pytest.mark.django_db
def test_reprocess_creates_a_new_run_with_its_own_stats(monkeypatch, user, tmp_path):
    first = MeteredLLM(
        [CallMetrics("results", seconds=1.0, output_tokens=10, eval_seconds=1.0)],
        header=SAMPLE_HEADER,
        results=sample_llm_results,
    )
    pdf = build_pdf(tmp_path / "laudo.pdf", [PAGE_1])
    document, run_one = run_task(monkeypatch, user, pdf, first)

    second = MeteredLLM(
        [CallMetrics("results", seconds=2.0, output_tokens=40, eval_seconds=2.0)],
        header=SAMPLE_HEADER,
        results=sample_llm_results,
    )
    monkeypatch.setattr(tasks, "build_llm_client", lambda: second)
    tasks.process_exam_document(document.pk, reprocess=True)

    run_two = document.runs.exclude(pk=run_one.pk).get()
    assert [d["output_tokens"] for d in run_one.stats["llm_calls_detail"]] == [10]
    assert [d["output_tokens"] for d in run_two.stats["llm_calls_detail"]] == [40]
    assert run_two.stats["llm_output_tokens"] == 40


# --- privacidade ---------------------------------------------------------------------------

FORBIDDEN = [
    # exames e valores do PDF sintético
    "Hemácias",
    "Hemoglobina",
    "Leucócitos",
    "Neutrófilos",
    "Glicose",
    "GLICOSE",
    "Creatinina",
    "CREATININA",
    "TSH",
    "Magnésio",
    "MAGNÉSIO",
    "Testosterona",
    "TESTOSTERONA",
    "4,85",
    "1,20 mg/dL",
    "1,942",
    "11,52",
    "mg/dL",
    # paciente, identificadores e laboratório
    "JOAO",
    "TESTE",
    "SILVA",
    "123.456.789-09",
    "99999999999",
    "DR EXEMPLO",
    "021-11111-222",
    "Laboratório",
    "EXEMPLO",
    "Material",
]


@pytest.mark.django_db
def test_stats_json_has_no_exam_names_values_or_patient_data(monkeypatch, user, sample_pdf):
    per_call = [
        CallMetrics(
            "results",
            seconds=8.4,
            load_seconds=1.0,
            prompt_tokens=500,
            output_tokens=90,
            eval_seconds=18.0,
        )
    ]
    llm = MeteredLLM(per_call, header=SAMPLE_HEADER, results=sample_llm_results)
    _, run = run_task(monkeypatch, user, sample_pdf, llm)

    assert run.status == RunStatus.SUCCESS and run.stats["llm_calls_detail"]
    dumped = json.dumps(run.stats, ensure_ascii=False)
    lowered = dumped.lower()
    for text in FORBIDDEN:
        assert text.lower() not in lowered, text
    # E o formato é exatamente o contrato: só números, enums e chaves fixas.
    for detail in run.stats["llm_calls_detail"]:
        assert set(detail) == DETAIL_KEYS
        assert detail["kind"] in {"header", "results", "match", "vision"}
        assert detail["outcome"] in {"ok", "timeout", "error"}
        for key in ("page", "seconds", "load_seconds", "prompt_tokens", "output_tokens"):
            assert detail[key] is None or isinstance(detail[key], int | float)


# --- API -----------------------------------------------------------------------------------


@pytest.mark.django_db
def test_runs_endpoint_exposes_the_new_keys_only_to_the_owner(
    monkeypatch, user, other_user, tmp_path
):
    per_call = [
        CallMetrics("results", seconds=6.0, output_tokens=30, eval_seconds=6.0, load_seconds=2.0)
    ]
    llm = MeteredLLM(per_call, header=SAMPLE_HEADER, results=qualitative_llm_results)
    document, _ = run_task(monkeypatch, user, qualitative_pdf(tmp_path, "NITRITO"), llm)

    owner = APIClient()
    owner.force_authenticate(user)
    response = owner.get(f"/api/v1/exam-documents/{document.pk}/runs/")
    assert response.status_code == 200
    (run,) = response.json()
    stats = run["stats"]
    assert stats["llm_calls_detail"][0]["tokens_per_second"] == 5.0
    assert stats["llm_output_tokens"] == 30
    assert stats["llm_avg_tokens_per_second"] == 5.0
    assert stats["llm_load_seconds"] == 2.0
    assert stats["llm_calls"] == 1  # chaves antigas seguem com o mesmo significado

    stranger = APIClient()
    stranger.force_authenticate(other_user)
    assert stranger.get(f"/api/v1/exam-documents/{document.pk}/runs/").status_code == 404
