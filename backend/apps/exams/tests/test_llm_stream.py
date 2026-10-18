"""Streaming do Ollama: montagem da resposta, erros e tempos limite.

Sem rede: a sessão HTTP é um stub que devolve linhas NDJSON prontas.
"""

import json

import pytest
import requests
from urllib3.exceptions import ReadTimeoutError

from apps.exams.codes import ErrorCode
from apps.exams.extraction import llm as llm_module
from apps.exams.extraction.errors import ExtractionError
from apps.exams.extraction.llm import OllamaClient


def ndjson(*objects) -> list[bytes]:
    return [json.dumps(obj).encode() for obj in objects]


def chunk(content: str, done: bool = False) -> dict:
    return {"message": {"role": "assistant", "content": content}, "done": done}


class StubResponse:
    status_code = 200
    text = ""

    def __init__(self, lines, *, then=None, status_code=200):
        self._lines = list(lines)
        self._then = then  # exceção levantada depois das linhas (queda/silêncio no meio)
        self.status_code = status_code
        self.closed = False

    def iter_lines(self, chunk_size=None):
        yield from self._lines
        if self._then is not None:
            raise self._then

    def json(self):
        return {}

    def close(self):
        self.closed = True


class StubSession:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.calls: list[dict] = []

    def post(self, url, **kwargs):
        self.calls.append({"url": url, **kwargs})
        if self.error is not None:
            raise self.error
        return self.response


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.delenv("AI_ALLOW_REMOTE_INFERENCE", raising=False)
    for name in (
        "OLLAMA_STALL_SECONDS",
        "OLLAMA_TIMEOUT_SECONDS",
        "OLLAMA_KEEP_ALIVE",
        "OLLAMA_NUM_PREDICT",
    ):
        monkeypatch.delenv(name, raising=False)


def client_with(response=None, error=None, **kwargs) -> tuple[OllamaClient, StubSession]:
    client = OllamaClient(**kwargs)
    session = StubSession(response, error)
    client._session = session
    return client, session


def chat(client: OllamaClient) -> str:
    return client._chat(model="m", prompt="p")


def test_stream_is_assembled_from_several_chunks():
    response = StubResponse(
        ndjson(chunk('{"re'), chunk('sults": '), chunk("[]"), chunk("}", done=True))
    )
    client, _ = client_with(response)
    assert chat(client) == '{"results": []}'
    assert response.closed


def test_blank_lines_between_chunks_are_ignored():
    response = StubResponse([b"", *ndjson(chunk("a")), b"", *ndjson(chunk("b", done=True))])
    client, _ = client_with(response)
    assert chat(client) == "ab"


def test_reading_stops_at_done():
    response = StubResponse(ndjson(chunk("a"), chunk("b", done=True), chunk("lixo")))
    client, _ = client_with(response)
    assert chat(client) == "ab"


def test_payload_streams_and_keeps_model_loaded():
    client, session = client_with(StubResponse(ndjson(chunk("{}", done=True))))
    client.match_analyte("Glicose", "mg/dL", [("glicose", "Glicose")])
    call = session.calls[0]
    assert call["json"]["stream"] is True
    assert call["json"]["keep_alive"] == "30m"
    assert call["json"]["options"] == {"temperature": 0, "num_ctx": client.num_ctx}
    assert "format" in call["json"]  # a resolução de exame continua com JSON Schema
    assert call["stream"] is True
    assert call["allow_redirects"] is False
    assert call["timeout"] == (10, 120)


RESULT_LINES = "1|Hemácias|4,85|milhões/uL|0|4,50 a 6,50 milhões/uL\n2|Hemoglobina||g/dL|0|"


def test_results_are_requested_as_plain_text_with_a_token_cap():
    client, session = client_with(StubResponse(ndjson(chunk(RESULT_LINES, done=True))))
    client.extract_results("Hemácias | 4,85 milhões/uL | 4,50 a 6,50 milhões/uL\nHemoglobina", 1)
    payload = session.calls[0]["json"]
    assert "format" not in payload  # sem gramática JSON: ela custava muito mais tokens
    assert payload["options"] == {
        "temperature": 0,
        "num_ctx": client.num_ctx,
        "num_predict": 1500,
    }
    prompt = payload["messages"][0]["content"]
    assert "[1] Hemácias | 4,85 milhões/uL" in prompt and "[2] Hemoglobina" in prompt
    assert payload["stream"] is True and payload["keep_alive"] == "30m"


def test_header_is_requested_as_plain_text_with_a_smaller_cap():
    client, session = client_with(StubResponse(ndjson(chunk("lab: X", done=True))))
    client.extract_header("texto")
    payload = session.calls[0]["json"]
    assert "format" not in payload
    assert payload["options"]["num_predict"] == 300


def test_num_predict_comes_from_environment(monkeypatch):
    monkeypatch.setenv("OLLAMA_NUM_PREDICT", "700")
    client, session = client_with(StubResponse(ndjson(chunk("", done=True))))
    assert client.num_predict == 700
    client.extract_results("linha", 1)
    assert session.calls[0]["json"]["options"]["num_predict"] == 700


def test_extract_results_parses_the_compact_lines_into_rows():
    client, _ = client_with(StubResponse(ndjson(chunk(RESULT_LINES, done=True))))
    block_text = "Hemácias | 4,85 milhões/uL | 4,50 a 6,50 milhões/uL\nHemoglobina | g/dL"
    output = client.extract_results(block_text, 1)
    assert [r["name"] for r in output["results"]] == ["Hemácias", "Hemoglobina"]
    assert output["results"][0] == {
        "name": "Hemácias",
        "value_text": "4,85",
        "unit": "milhões/uL",
        "reference_text": "4,50 a 6,50 milhões/uL",
        "printed_flag": "",
        "is_calculated": False,
        "source_line": "Hemácias | 4,85 milhões/uL | 4,50 a 6,50 milhões/uL",
    }
    assert output["results"][1]["value_text"] == ""


def test_extract_results_with_unusable_text_returns_no_rows_instead_of_failing():
    client, _ = client_with(StubResponse(ndjson(chunk("Não há resultados.", done=True))))
    assert client.extract_results("linha", 1) == {"results": []}


def test_extract_header_parses_the_seven_lines():
    text = "\n".join(
        [
            "lab: Laboratório Exemplo",
            "accession: 021-11111-222",
            "patient: JOAO TESTE",
            "birth: 05/03/1977",
            "physician:",
            "issued: 31/08/2026 - 13:57:40",
            "collected: 25/08/2026 - 07:01:51",
        ]
    )
    client, _ = client_with(StubResponse(ndjson(chunk(text, done=True))))
    header = client.extract_header("texto")
    assert header["laboratory_name"] == "Laboratório Exemplo"
    assert header["ordering_physician"] == ""
    assert header["collected_at"] == "25/08/2026 - 07:01:51"


def test_timeouts_and_keep_alive_come_from_environment(monkeypatch):
    monkeypatch.setenv("OLLAMA_STALL_SECONDS", "45")
    monkeypatch.setenv("OLLAMA_TIMEOUT_SECONDS", "900")
    monkeypatch.setenv("OLLAMA_KEEP_ALIVE", "5m")
    client, session = client_with(StubResponse(ndjson(chunk("x", done=True))))
    chat(client)
    assert client.timeout == 900
    assert session.calls[0]["timeout"] == (10, 45)
    assert session.calls[0]["json"]["keep_alive"] == "5m"


def test_defaults_are_120s_stall_and_1800s_wall_clock():
    client = OllamaClient()
    assert (client.stall_timeout, client.timeout) == (120, 1800)


def test_model_not_found_error_line():
    response = StubResponse(ndjson({"error": "model 'x' not found"}))
    client, _ = client_with(response)
    with pytest.raises(ExtractionError) as info:
        chat(client)
    assert info.value.code == ErrorCode.MODEL_NOT_FOUND
    assert info.value.params == {"model": "m"}


def test_other_error_line_is_inference_unavailable_without_params():
    response = StubResponse(ndjson(chunk("a"), {"error": "falha interna"}))
    client, _ = client_with(response)
    with pytest.raises(ExtractionError) as info:
        chat(client)
    assert info.value.code == ErrorCode.INFERENCE_UNAVAILABLE
    assert info.value.params == {}


@pytest.mark.parametrize("bad", [b"isto nao e json", b"\xff\xfe", b"[1, 2]"])
def test_undecodable_line_is_invalid_model_output(bad):
    response = StubResponse([*ndjson(chunk("a")), bad])
    client, _ = client_with(response)
    with pytest.raises(ExtractionError) as info:
        chat(client)
    assert info.value.code == ErrorCode.INVALID_MODEL_OUTPUT
    assert response.closed


def test_stream_without_done_is_inference_unavailable():
    client, _ = client_with(StubResponse(ndjson(chunk("a"), chunk("b"))))
    with pytest.raises(ExtractionError) as info:
        chat(client)
    assert info.value.code == ErrorCode.INFERENCE_UNAVAILABLE


def test_read_timeout_on_request_is_inference_timeout():
    client, _ = client_with(error=requests.ReadTimeout("silêncio"))
    with pytest.raises(ExtractionError) as info:
        chat(client)
    assert info.value.code == ErrorCode.INFERENCE_TIMEOUT
    assert info.value.params == {}


def test_connect_timeout_is_inference_timeout():
    client, _ = client_with(error=requests.ConnectTimeout("sem resposta"))
    with pytest.raises(ExtractionError) as info:
        chat(client)
    assert info.value.code == ErrorCode.INFERENCE_TIMEOUT


def test_read_timeout_in_the_middle_of_the_stream_is_inference_timeout():
    response = StubResponse(ndjson(chunk("a")), then=requests.ReadTimeout("silêncio"))
    client, _ = client_with(response)
    with pytest.raises(ExtractionError) as info:
        chat(client)
    assert info.value.code == ErrorCode.INFERENCE_TIMEOUT
    assert response.closed


def test_urllib3_read_timeout_wrapped_by_requests_is_inference_timeout():
    # `requests` converte o ReadTimeoutError do urllib3 em ConnectionError durante o stream.
    wrapped = requests.ConnectionError(ReadTimeoutError(None, None, "read timed out"))  # type: ignore[arg-type]
    client, _ = client_with(StubResponse(ndjson(chunk("a")), then=wrapped))
    with pytest.raises(ExtractionError) as info:
        chat(client)
    assert info.value.code == ErrorCode.INFERENCE_TIMEOUT


def test_connection_refused_is_inference_unavailable():
    client, _ = client_with(error=requests.ConnectionError("recusada"))
    with pytest.raises(ExtractionError) as info:
        chat(client)
    assert info.value.code == ErrorCode.INFERENCE_UNAVAILABLE
    assert info.value.params == {}


def test_connection_dropped_in_the_middle_of_the_stream_is_inference_unavailable():
    response = StubResponse(ndjson(chunk("a")), then=requests.ConnectionError("caiu"))
    client, _ = client_with(response)
    with pytest.raises(ExtractionError) as info:
        chat(client)
    assert info.value.code == ErrorCode.INFERENCE_UNAVAILABLE


def test_wall_clock_cap_is_checked_while_tokens_keep_arriving(monkeypatch):
    # Os pedaços chegam sem parar (o silêncio nunca dispara), mas o teto por chamada vale.
    now = [1000.0]
    monkeypatch.setattr(llm_module, "_now", lambda: now[0])

    class TickingResponse(StubResponse):
        def iter_lines(self, chunk_size=None):
            for line in self._lines:
                now[0] += 100  # cada pedaço leva 100 s
                yield line

    response = TickingResponse(ndjson(*[chunk("x") for _ in range(10)], chunk("y", done=True)))
    client, _ = client_with(response, timeout=250)
    with pytest.raises(ExtractionError) as info:
        chat(client)
    assert info.value.code == ErrorCode.INFERENCE_TIMEOUT
    assert response.closed


def test_long_generation_within_the_cap_succeeds(monkeypatch):
    now = [0.0]
    monkeypatch.setattr(llm_module, "_now", lambda: now[0])

    class TickingResponse(StubResponse):
        def iter_lines(self, chunk_size=None):
            for line in self._lines:
                now[0] += 10
                yield line

    response = TickingResponse(ndjson(*[chunk("x") for _ in range(9)], chunk("y", done=True)))
    client, _ = client_with(response, timeout=1800)
    assert chat(client) == "x" * 9 + "y"


def test_http_error_status_is_still_mapped():
    client, _ = client_with(StubResponse([], status_code=500))
    with pytest.raises(ExtractionError) as info:
        chat(client)
    assert info.value.code == ErrorCode.INFERENCE_UNAVAILABLE


def test_json_chat_returns_parsed_dict_from_stream():
    response = StubResponse(ndjson(chunk('{"slug": '), chunk('"glicose"}', done=True)))
    client, _ = client_with(response)
    assert client.match_analyte("Glicose", "mg/dL", [("glicose", "Glicose")]) == "glicose"


def test_json_chat_retries_once_on_malformed_json_then_gives_up():
    responses = [
        StubResponse(ndjson(chunk("nao eh json", done=True))),
        StubResponse(ndjson(chunk("{quebrado", done=True))),
    ]
    client = OllamaClient()
    session = StubSession()
    session.post = lambda url, **kw: responses.pop(0)
    client._session = session
    with pytest.raises(ExtractionError) as info:
        client.match_analyte("Glicose", "mg/dL", [("glicose", "Glicose")])
    assert info.value.code == ErrorCode.INVALID_MODEL_OUTPUT
    assert responses == []
