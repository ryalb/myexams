"""PDF -> linhas de texto por página.

Laudos são tabelas, e a extração de texto simples embaralha colunas. Aqui as
palavras são agrupadas por posição vertical e ordenadas por x; um salto
horizontal grande vira " | ", que marca a quebra de coluna para o LLM. Páginas
sem camada de texto são transcritas por um modelo de visão (laudos escaneados).
"""

import io
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from ..codes import ErrorCode
from .errors import ExtractionError

COLUMN_GAP_POINTS = 14
LINE_TOLERANCE_POINTS = 3
MIN_TEXT_CHARS = 30
RENDER_DPI = 200

# Limites contra PDFs hostis (página gigante / milhões de palavras) antes de renderizar.
MAX_PAGE_SIDE_POINTS = 2500
MAX_WORDS_PER_PAGE = 20000

# Redação direcionada: só o que identifica a pessoa. Sequências de dígitos sem rótulo
# (número do pedido, resultados grandes) são dado legítimo e ficam intactas.
_EMAIL = re.compile(r"[\w.+\-]+@[\w\-]+(?:\.[\w\-]+)+")
_CPF = re.compile(r"(?<!\d)\d{3}[.\s]\d{3}[.\s]\d{3}[\-\s]\d{2}(?!\d)")
_DOC_LABELS = r"CPF|RG|R\.\s?G\.?|CNS|Cart[ãa]o\s+(?:Nacional\s+de\s+)?SUS|SUS|Identidade|Documento"
_LABELLED_DOC = re.compile(
    r"(?<!\w)(?P<label>" + _DOC_LABELS + r")(?!\w)"
    r"(?P<sep>\s*(?:n[º°o]\.?\s*)?[:\-]?\s*\|?\s*)"
    r"(?P<value>(?:[A-Za-z]{2}-?)?\d[\d.\-]{2,}[0-9Xx]?)",
    re.IGNORECASE,
)
_CNS = re.compile(r"(?<!\d)(?:\d{15}|\d{3}\s\d{4}\s\d{4}\s\d{4})(?!\d)")
_PHONE_NUMBER = r"(?:\(?\d{2}\)?\s?)?\d{4,5}-?\d{4}"
_LABELLED_PHONE = re.compile(
    r"(?<!\w)(?P<label>Tel(?:efone)?|Fone|Celular|WhatsApp|Contato)(?!\w)"
    r"(?P<sep>\.?\s*:?\s*\|?\s*)(?P<value>" + _PHONE_NUMBER + r")(?!\d)",
    re.IGNORECASE,
)
_PAREN_PHONE = re.compile(r"\(\d{2}\)\s?\d{4,5}-?\d{4}(?!\d)")
_HEX_LINE = re.compile(r"^[0-9A-Fa-f]{32,}$")
# Glifo sem mapeamento (logotipo/ornamento): o pdfminer imprime `(cid:0)(cid:0)...`.
_CID_LINE = re.compile(r"^(?:\(cid:\d+\))+$")
MINUS_SIGN = "\u2212"  # alguns laudos imprimem "−" nas faixas ("4.5 − 6.1") e nos números


@dataclass
class PageText:
    number: int
    lines: list[str]
    method: str  # "text" | "vision"


def _placeholder_for(label: str) -> str:
    normalized = re.sub(r"[\s.]", "", label).upper()
    if normalized == "CPF":
        return "[CPF]"
    return "[RG]" if normalized == "RG" else "[ID]"


def redact(line: str) -> str:
    """Remove identificadores pessoais antes de o texto ir ao LLM ou ao banco.

    Regras direcionadas (CPF, documentos rotulados, CNS, e-mail, telefone); números
    soltos sem rótulo, como o número do pedido, são preservados.
    """
    line = _EMAIL.sub("[EMAIL]", line)
    line = _CPF.sub("[CPF]", line)
    line = _LABELLED_DOC.sub(
        lambda m: f"{m['label']}{m['sep']}{_placeholder_for(m['label'])}", line
    )
    line = _CNS.sub("[ID]", line)
    line = _LABELLED_PHONE.sub(lambda m: f"{m['label']}{m['sep']}[TEL]", line)
    return _PAREN_PHONE.sub("[TEL]", line)


def clean_lines(raw_lines: list[str]) -> list[str]:
    cleaned = []
    for raw in raw_lines:
        line = re.sub(r"[ \t]+", " ", raw.replace(MINUS_SIGN, "-")).strip()
        if not line or _HEX_LINE.match(line.replace(" ", "")) or _CID_LINE.match(line):
            continue
        cleaned.append(redact(line))
    return cleaned


def words_to_lines(words: list[dict], gap: float = COLUMN_GAP_POINTS) -> list[str]:
    """Agrupa palavras (com x0/x1/top) em linhas, marcando colunas com ' | '."""
    if not words:
        return []
    ordered = sorted(words, key=lambda w: (w["top"], w["x0"]))
    rows: list[list[dict]] = []
    current_top = None
    for word in ordered:
        if current_top is None or abs(word["top"] - current_top) > LINE_TOLERANCE_POINTS:
            rows.append([word])
            current_top = word["top"]
        else:
            rows[-1].append(word)
    lines = []
    for row in rows:
        row.sort(key=lambda w: w["x0"])
        parts = [row[0]["text"]]
        for previous, word in zip(row, row[1:], strict=False):
            separator = " | " if word["x0"] - previous["x1"] > gap else " "
            parts.append(separator + word["text"])
        lines.append("".join(parts))
    return lines


def open_pdf(path: Path):
    import pdfplumber

    try:
        return pdfplumber.open(path)
    except Exception as exc:  # pdfminer lança vários tipos
        name = type(exc).__name__
        if "Password" in name or "Encrypt" in name:
            raise ExtractionError(ErrorCode.PDF_ENCRYPTED) from exc
        raise ExtractionError(ErrorCode.PDF_UNREADABLE) from exc


def count_pages(path: Path) -> int:
    with open_pdf(path) as pdf:
        return len(pdf.pages)


def _check_page_size(page) -> None:
    """Recusa MediaBox absurda antes de qualquer renderização (bomba de descompressão)."""
    try:
        x0, y0, x1, y1 = (float(v) for v in page.mediabox)
    except Exception as exc:
        raise ExtractionError(ErrorCode.PDF_UNREADABLE) from exc
    if abs(x1 - x0) > MAX_PAGE_SIDE_POINTS or abs(y1 - y0) > MAX_PAGE_SIDE_POINTS:
        raise ExtractionError(ErrorCode.PDF_UNREADABLE)


def read_pages(
    path: Path,
    transcribe,
    max_pages: int,
    on_vision: Callable[[int], None] | None = None,
) -> list[PageText]:
    """Lê cada página; usa `transcribe(png_bytes) -> str` quando não há texto.

    `on_vision(número_da_página)` é chamado logo antes de cada transcrição por visão.
    """
    pages: list[PageText] = []
    with open_pdf(path) as pdf:
        if len(pdf.pages) > max_pages:
            raise ExtractionError(ErrorCode.FILE_TOO_MANY_PAGES, {"max": max_pages})
        for index, page in enumerate(pdf.pages, start=1):
            _check_page_size(page)
            try:
                words = page.extract_words(x_tolerance=1.5, y_tolerance=LINE_TOLERANCE_POINTS)
            except Exception as exc:
                raise ExtractionError(ErrorCode.PDF_UNREADABLE) from exc
            if len(words) > MAX_WORDS_PER_PAGE:
                raise ExtractionError(ErrorCode.PDF_UNREADABLE)
            text_chars = sum(len(w["text"]) for w in words)
            if text_chars >= MIN_TEXT_CHARS:
                pages.append(PageText(index, clean_lines(words_to_lines(words)), "text"))
                continue
            image = page.to_image(resolution=RENDER_DPI).original
            buffer = io.BytesIO()
            image.save(buffer, format="PNG")
            if on_vision is not None:
                on_vision(index)
            transcript = transcribe(buffer.getvalue())
            pages.append(PageText(index, clean_lines(transcript.splitlines()), "vision"))
    return pages
