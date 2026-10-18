"""Normalização de texto compartilhada pelo pipeline."""

import re
import unicodedata

_SPACES = re.compile(r"\s+")


def _base_char(char: str) -> str:
    decomposed = unicodedata.normalize("NFD", char)
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    return stripped[:1] if stripped else char


def fold(text: str) -> str:
    """Minúsculas e sem acentos, **preservando o comprimento**.

    Os índices do texto dobrado valem para o original, então dá para casar no
    dobrado e fatiar o original (ex.: extrair o rótulo de uma faixa).
    """
    return "".join(_base_char(c) for c in text.lower())


def normalize_name(text: str) -> str:
    """Chave de comparação de nomes de exame: sem acento, pontuação nem caixa."""
    folded = fold(text)
    folded = re.sub(r"[^a-z0-9%]+", " ", folded)
    return _SPACES.sub(" ", folded).strip()


def sorted_tokens_key(normalized: str) -> str:
    """Mesma chave com tokens ordenados: 'hdl colesterol' == 'colesterol hdl'."""
    return " ".join(sorted(normalized.split()))


def compact(text: str) -> str:
    """Sem espaço nenhum, para checar se um valor aparece no texto da página."""
    return re.sub(r"\s+", "", fold(text))
