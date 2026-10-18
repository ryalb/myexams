"""Cliente de inferência local (Ollama) e a interface que o pipeline consome.

Saúde é dado sensível: a inferência só roda local. Um `OLLAMA_URL` que não seja
da própria máquina é recusado, a menos que `AI_ALLOW_REMOTE_INFERENCE=true`.
"""

import base64
import json
import logging
import os
import time
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urlparse

import requests
from urllib3.exceptions import ReadTimeoutError

from ..codes import ErrorCode
from .blocks import Block
from .compact import parse_compact, parse_header_lines
from .errors import ExtractionError
from .schema import HEADER_PROMPT, LINES_PROMPT, MATCH_PROMPT, MATCH_SCHEMA, VISION_PROMPT

logger = logging.getLogger(__name__)

LOCAL_INFERENCE_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", "0.0.0.0", "ollama"})

CONNECT_TIMEOUT_SECONDS = 10
DEFAULT_STALL_SECONDS = 120
DEFAULT_TIMEOUT_SECONDS = 1800
DEFAULT_NUM_PREDICT = 1500
# O cabeçalho são sete linhas curtas: teto próprio, bem menor que o dos resultados.
HEADER_NUM_PREDICT = 300


def _now() -> float:
    """Relógio monotônico (isolado para os testes simularem o tempo)."""
    return time.monotonic()


@dataclass(frozen=True)
class CallMetrics:
    """Medidas de UMA chamada ao modelo. Só números e enums: nunca prompt nem resposta.

    `kind`: header | results | match | vision. `outcome`: ok | timeout | error.
    `eval_seconds` é o tempo de geração medido pelo próprio Ollama (`eval_duration`).
    """

    kind: str
    seconds: float
    load_seconds: float | None = None
    prompt_tokens: int | None = None
    output_tokens: int | None = None
    eval_seconds: float | None = None
    outcome: str = "ok"


# Únicos campos do pedaço final do Ollama que interessam (nunca o conteúdo).
_METRIC_KEYS = (
    "load_duration",
    "prompt_eval_count",
    "eval_count",
    "eval_duration",
)


def _ns_to_seconds(value: object) -> float | None:
    """Duração em nanossegundos do Ollama -> segundos (None se ausente ou inválida)."""
    if isinstance(value, bool) or not isinstance(value, int | float) or value < 0:
        return None
    return value / 1e9


def _count(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def assert_local_inference(url: str) -> None:
    if os.getenv("AI_ALLOW_REMOTE_INFERENCE", "").lower() == "true":
        return
    try:
        host = urlparse(url).hostname
    except ValueError:
        host = None
    if host not in LOCAL_INFERENCE_HOSTS:
        raise ExtractionError(ErrorCode.INFERENCE_NOT_LOCAL, {"host": host or ""})


class LLMClient(Protocol):
    model: str
    vision_model: str

    def extract_header(self, text: str) -> dict: ...

    def extract_results(self, block_text: str, page: int) -> dict: ...

    def transcribe_page(self, png: bytes) -> str: ...

    def match_analyte(self, name: str, unit: str, candidates: list[tuple[str, str]]) -> str: ...


class OllamaClient:
    """Cliente do Ollama com resposta em streaming.

    Sem streaming nada chega até o modelo terminar, então o tempo limite de leitura
    equivale ao tempo total de geração (um bloco grande estoura). Com streaming os
    tokens chegam continuamente: `stall_timeout` é só de **inatividade** entre dois
    pedaços e `timeout` é o teto de relógio de parede de cada chamada.
    """

    def __init__(
        self,
        url: str | None = None,
        model: str | None = None,
        vision_model: str | None = None,
        num_ctx: int | None = None,
        timeout: int | None = None,
        stall_timeout: int | None = None,
        keep_alive: str | None = None,
        num_predict: int | None = None,
    ) -> None:
        self.url = (url or os.getenv("OLLAMA_URL", "http://localhost:11434")).rstrip("/")
        self.model = model or os.getenv("OLLAMA_CHAT_MODEL", "qwen2.5:14b")
        self.vision_model = vision_model or os.getenv("OLLAMA_VISION_MODEL", "qwen2.5vl:7b")
        self.num_ctx = num_ctx or int(os.getenv("OLLAMA_NUM_CTX", "4096"))
        # Teto de relógio de parede POR chamada.
        self.timeout = timeout or int(
            os.getenv("OLLAMA_TIMEOUT_SECONDS", str(DEFAULT_TIMEOUT_SECONDS))
        )
        # Silêncio máximo entre dois pedaços da resposta.
        self.stall_timeout = stall_timeout or int(
            os.getenv("OLLAMA_STALL_SECONDS", str(DEFAULT_STALL_SECONDS))
        )
        # Mantém o modelo carregado entre os blocos (recarregar custa dezenas de segundos).
        self.keep_alive = keep_alive or os.getenv("OLLAMA_KEEP_ALIVE", "30m")
        # Teto de tokens gerados por chamada de texto compacto (resultados/cabeçalho).
        self.num_predict = num_predict or int(
            os.getenv("OLLAMA_NUM_PREDICT", str(DEFAULT_NUM_PREDICT))
        )
        assert_local_inference(self.url)
        # Uma entrada por chamada ao modelo (inclusive as que falharam); `drain_metrics` esvazia.
        self.metrics: list[CallMetrics] = []
        # Sessão própria: `trust_env=False` ignora HTTP(S)_PROXY/NETRC do ambiente, para que
        # o tráfego de inferência (dado de saúde) não seja desviado para um proxy.
        self._session = requests.Session()
        self._session.trust_env = False

    def _chat(
        self,
        *,
        model: str,
        prompt: str,
        schema: dict | None = None,
        png: bytes | None = None,
        num_predict: int | None = None,
        kind: str = "results",
    ) -> str:
        started = _now()
        final: dict = {}
        try:
            content = self._request(model, prompt, schema, png, num_predict, final)
        except Exception as exc:
            timed_out = isinstance(exc, ExtractionError) and exc.code == ErrorCode.INFERENCE_TIMEOUT
            self._record(kind, started, final, "timeout" if timed_out else "error")
            raise
        self._record(kind, started, final, "ok")
        return content

    def _record(self, kind: str, started: float, final: dict, outcome: str) -> None:
        self.metrics.append(
            CallMetrics(
                kind=kind,
                seconds=max(0.0, _now() - started),
                load_seconds=_ns_to_seconds(final.get("load_duration")),
                prompt_tokens=_count(final.get("prompt_eval_count")),
                output_tokens=_count(final.get("eval_count")),
                eval_seconds=_ns_to_seconds(final.get("eval_duration")),
                outcome=outcome,
            )
        )

    def drain_metrics(self) -> list[CallMetrics]:
        """Devolve as medidas acumuladas desde a última chamada e as descarta."""
        drained, self.metrics = self.metrics, []
        return drained

    def _request(
        self,
        model: str,
        prompt: str,
        schema: dict | None,
        png: bytes | None,
        num_predict: int | None,
        final: dict,
    ) -> str:
        message: dict = {"role": "user", "content": prompt}
        if png is not None:
            message["images"] = [base64.b64encode(png).decode("ascii")]
        options: dict = {"temperature": 0, "num_ctx": self.num_ctx}
        if num_predict is not None:
            options["num_predict"] = num_predict
        payload: dict = {
            "model": model,
            "stream": True,
            "keep_alive": self.keep_alive,
            "messages": [message],
            # num_ctx explícito: o default do Ollama trunca blocos longos em silêncio.
            "options": options,
        }
        if schema is not None:
            payload["format"] = schema
        deadline = _now() + self.timeout
        try:
            # Sem redirecionamento: uma resposta 3xx não pode levar o laudo para outro host.
            response = self._session.post(
                f"{self.url}/api/chat",
                json=payload,
                timeout=(CONNECT_TIMEOUT_SECONDS, self.stall_timeout),
                allow_redirects=False,
                stream=True,
            )
        except requests.RequestException as exc:
            raise _transport_error(exc) from exc

        try:
            return self._read_stream(response, model, deadline, final)
        finally:
            response.close()

    def _read_stream(
        self,
        response: requests.Response,
        model: str,
        deadline: float,
        final: dict | None = None,
    ) -> str:
        """Monta a resposta; o último pedaço (`done`) é copiado para `final` (só as medidas)."""
        if 300 <= response.status_code < 400:
            raise ExtractionError(ErrorCode.INFERENCE_UNAVAILABLE)
        if response.status_code >= 400:
            raise _error_for(_error_detail(response), model)

        parts: list[str] = []
        try:
            for line in response.iter_lines(chunk_size=None):
                if _now() > deadline:
                    raise ExtractionError(ErrorCode.INFERENCE_TIMEOUT)
                if not line:
                    continue
                try:
                    chunk = json.loads(line)
                except ValueError as exc:  # JSONDecodeError e UnicodeDecodeError
                    raise ExtractionError(ErrorCode.INVALID_MODEL_OUTPUT) from exc
                if not isinstance(chunk, dict):
                    raise ExtractionError(ErrorCode.INVALID_MODEL_OUTPUT)
                if "error" in chunk:
                    raise _error_for(str(chunk["error"]), model)
                content = (chunk.get("message") or {}).get("content")
                if isinstance(content, str):
                    parts.append(content)
                if chunk.get("done"):
                    if final is not None:
                        final.update({key: chunk.get(key) for key in _METRIC_KEYS})
                    return "".join(parts)
        except requests.RequestException as exc:
            raise _transport_error(exc) from exc
        except (TimeoutError, ReadTimeoutError) as exc:
            raise ExtractionError(ErrorCode.INFERENCE_TIMEOUT) from exc
        # O fluxo acabou sem `done`: o Ollama caiu no meio da geração.
        raise ExtractionError(ErrorCode.INFERENCE_UNAVAILABLE)

    def _json_chat(self, prompt: str, schema: dict, kind: str = "match") -> dict:
        """JSON validado pelo schema; uma nova tentativa antes de desistir."""
        for _ in range(2):
            content = self._chat(model=self.model, prompt=prompt, schema=schema, kind=kind)
            try:
                data = json.loads(content)
            except json.JSONDecodeError:
                continue
            if isinstance(data, dict):
                return data
        raise ExtractionError(ErrorCode.INVALID_MODEL_OUTPUT)

    def extract_header(self, text: str) -> dict:
        """Cabeçalho em sete linhas `chave: valor`, sem gramática JSON."""
        content = self._chat(
            model=self.model,
            prompt=HEADER_PROMPT.format(text=text),
            num_predict=min(self.num_predict, HEADER_NUM_PREDICT),
            kind="header",
        )
        return parse_header_lines(content)

    def extract_results(self, block_text: str, page: int) -> dict:
        """Uma linha `n|nome|valor|unidade|calc|referência` por grandeza (texto simples).

        Devolve `{"results": [linhas]}`, o mesmo formato de antes; `source_line` é a linha
        numerada do trecho de onde o modelo disse ter lido o valor.
        """
        lines = block_text.split("\n")
        numbered = "\n".join(f"[{number}] {line}" for number, line in enumerate(lines, start=1))
        content = self._chat(
            model=self.model,
            prompt=LINES_PROMPT.format(text=numbered, page=page),
            num_predict=self.num_predict,
            kind="results",
        )
        parsed = parse_compact(content, Block(title="", lines=lines, page=page))
        if parsed.skipped:
            logger.info("Linhas da resposta do modelo descartadas: %s", parsed.skipped)
        return {"results": parsed.rows}

    def transcribe_page(self, png: bytes) -> str:
        return self._chat(model=self.vision_model, prompt=VISION_PROMPT, png=png, kind="vision")

    def match_analyte(self, name: str, unit: str, candidates: list[tuple[str, str]]) -> str:
        listing = "\n".join(f"{slug}: {label}" for slug, label in candidates)
        data = self._json_chat(
            MATCH_PROMPT.format(name=name, unit=unit, candidates=listing), MATCH_SCHEMA
        )
        return str(data.get("slug") or "")


def _error_for(detail: str, model: str) -> ExtractionError:
    if "not found" in detail.lower():
        return ExtractionError(ErrorCode.MODEL_NOT_FOUND, {"model": model})
    return ExtractionError(ErrorCode.INFERENCE_UNAVAILABLE)


def _transport_error(exc: requests.RequestException) -> ExtractionError:
    """Erro de rede -> código. Timeout (conexão ou silêncio) é distinto de "fora do ar".

    Sem params: a URL pode embutir credenciais.
    """
    if isinstance(exc, requests.exceptions.Timeout) or _wraps_read_timeout(exc):
        return ExtractionError(ErrorCode.INFERENCE_TIMEOUT)
    return ExtractionError(ErrorCode.INFERENCE_UNAVAILABLE)


def _wraps_read_timeout(exc: BaseException) -> bool:
    """Durante o streaming o `requests` converte o ReadTimeoutError do urllib3 em
    `ConnectionError(<ReadTimeoutError>)` (não em `ReadTimeout`)."""
    return any(isinstance(arg, ReadTimeoutError) for arg in exc.args) or isinstance(
        exc.__context__, ReadTimeoutError
    )


def _error_detail(response: requests.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        return (response.text or "")[:300]
    return str(payload.get("error", "")) if isinstance(payload, dict) else ""


def build_llm_client() -> LLMClient:
    return OllamaClient()
