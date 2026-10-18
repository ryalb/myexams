"""Telemetria das chamadas ao modelo em UMA execução de extração.

Só números e enums (duração, tokens, tokens/s, carga do modelo, desfecho): nunca prompt,
resposta, nome de exame, valor ou texto de página. O resultado vai para
`ExtractionRun.stats`, para o usuário ver na tela por que uma leitura está lenta.
"""

from .llm import CallMetrics

# Quantas chamadas ficam em `llm_calls_detail` (as mais antigas além do teto são descartadas;
# os totais continuam contando todas).
MAX_CALL_DETAILS = 200

OUTCOME_OK = "ok"
OUTCOME_TIMEOUT = "timeout"
OUTCOME_ERROR = "error"


def _round(value: float | None) -> float | None:
    return None if value is None else round(value, 2)


class LLMTelemetry:
    def __init__(self) -> None:
        self.details: list[dict] = []
        self._output_tokens = 0
        self._rated_tokens = 0
        self._rated_seconds = 0.0
        self._load_seconds = 0.0

    def record(self, metrics: CallMetrics, page: int | None) -> None:
        tokens_per_second = None
        if metrics.output_tokens is not None and metrics.eval_seconds:
            tokens_per_second = metrics.output_tokens / metrics.eval_seconds
            self._rated_tokens += metrics.output_tokens
            self._rated_seconds += metrics.eval_seconds
        self._output_tokens += metrics.output_tokens or 0
        self._load_seconds += metrics.load_seconds or 0.0
        self.details.append(
            {
                "kind": metrics.kind,
                "page": page,
                "seconds": round(metrics.seconds, 2),
                "load_seconds": _round(metrics.load_seconds),
                "prompt_tokens": metrics.prompt_tokens,
                "output_tokens": metrics.output_tokens,
                "tokens_per_second": _round(tokens_per_second),
                "outcome": metrics.outcome,
            }
        )
        if len(self.details) > MAX_CALL_DETAILS:
            del self.details[: len(self.details) - MAX_CALL_DETAILS]

    def record_call(
        self, llm: object, kind: str, page: int | None, seconds: float, outcome: str
    ) -> None:
        """Registra o que o cliente mediu na chamada que acabou de terminar.

        Clientes sem `drain_metrics` (fakes) só têm o tempo de parede: sem tokens nem carga.
        """
        drain = getattr(llm, "drain_metrics", None)
        measured = list(drain()) if callable(drain) else []
        if not measured:
            measured = [CallMetrics(kind=kind, seconds=seconds, outcome=outcome)]
        for metrics in measured:
            self.record(metrics, page)

    def stats(self) -> dict:
        average = self._rated_tokens / self._rated_seconds if self._rated_seconds else None
        return {
            "llm_calls_detail": list(self.details),
            "llm_output_tokens": self._output_tokens,
            "llm_avg_tokens_per_second": _round(average),
            "llm_load_seconds": round(self._load_seconds, 2),
        }
