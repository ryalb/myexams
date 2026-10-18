"""LLM em memória para testes e para o modo offline do avaliador."""

from collections.abc import Callable

from .llm import CallMetrics


class FakeLLMClient:
    model = "fake-model"
    vision_model = "fake-vision"

    def __init__(
        self,
        header: dict | None = None,
        # Recebe o texto do bloco; pode levantar `ExtractionError` para simular falha do modelo.
        results: Callable[[str], dict] | None = None,
        vision_text: str = "",
        matches: dict[str, str] | None = None,
    ) -> None:
        self.header = header or {}
        self.results = results or (lambda _text: {"results": []})
        self.vision_text = vision_text
        self.matches = matches or {}
        self.result_calls: list[str] = []
        self.header_calls: list[str] = []
        self.vision_calls = 0
        # Gancho de teste: medidas que `drain_metrics` devolve (e esvazia) após uma chamada.
        self.fake_metrics: list[CallMetrics] = []

    def drain_metrics(self) -> list[CallMetrics]:
        drained, self.fake_metrics = self.fake_metrics, []
        return drained

    def extract_header(self, text: str) -> dict:
        self.header_calls.append(text)
        return self.header

    def extract_results(self, block_text: str, page: int) -> dict:
        self.result_calls.append(block_text)
        return self.results(block_text)

    def transcribe_page(self, png: bytes) -> str:
        self.vision_calls += 1
        return self.vision_text

    def match_analyte(self, name: str, unit: str, candidates: list[tuple[str, str]]) -> str:
        return self.matches.get(name, "")
