"""Saída compacta do LLM em texto simples (sem gramática JSON).

Gerar JSON restrito custa muitos tokens (chaves, aspas, o texto inteiro da referência) e a
~4,5 tok/s isso inviabiliza um laudo. O modelo responde uma linha por grandeza:

    n|nome|valor|unidade|calc|referência

e o código reconstrói as linhas. O parser é tolerante: lixo, prefácio e cercas de markdown
são ignorados, e uma linha ruim nunca derruba as demais.
"""

import re
from dataclasses import dataclass, field

from .blocks import Block, find_source_line

MAX_ROWS = 60
SEPARATORS = 5  # n|nome|valor|unidade|calc|referência
_COLUMN = re.compile(r"\s*\|\s*")
# O modelo separa as faixas de uma mesma referência com ";" (ou com a sequência literal \n).
_REFERENCE_BREAK = re.compile(r"\\n|;")
_TRUE = frozenset({"1", "sim", "true", "s", "yes"})

HEADER_KEYS = {
    "lab": "laboratory_name",
    "accession": "accession_number",
    "patient": "patient_name",
    "birth": "patient_birth_date",
    "physician": "ordering_physician",
    "issued": "issued_at",
    "collected": "collected_at",
}
_HEADER_LINE = re.compile(r"^[\s>*\-•`_]*(?P<key>[a-z]+)[\s*_`]*:\s*(?P<value>.*)$", re.IGNORECASE)
_EMPTY_VALUES = frozenset({"", "-", "—", "–", '""', "''"})


@dataclass
class CompactParse:
    rows: list[dict] = field(default_factory=list)
    skipped: int = 0  # linhas com cara de resultado, mas inutilizáveis


def _digits(raw: str) -> str:
    return raw.strip().strip("[]().:# ")


def _line_number(raw: str, count: int) -> int | None:
    text = _digits(raw)
    if not text.isdigit():
        return None
    number = int(text)
    return number if 1 <= number <= count else None


def parse_compact(text: str, block: Block) -> CompactParse:
    """`n|nome|valor|unidade|calc|referência` por linha -> linhas de resultado."""
    result = CompactParse()
    for raw_line in (text or "").splitlines():
        line = raw_line.strip()
        # Tabela markdown ("| 1 | nome |..."): o pipe inicial só vale se o 1º campo for o número.
        # Com n vazio ("|nome|...") o pipe inicial já é um separador.
        if line.startswith("|") and _digits(line[1:].split("|", 1)[0]).isdigit():
            line = line[1:]
        if line.count("|") < SEPARATORS:
            continue  # prefácio, cerca ```, linha em branco ou texto solto
        number, name, value, unit, calc, reference = (p.strip() for p in line.split("|", 5))
        if number.lower() == "n" and name.lower() in {"nome", "name"}:
            continue  # o modelo repetiu o cabeçalho do formato
        if not any(ch.isalnum() for ch in name):
            result.skipped += 1
            continue
        if len(result.rows) >= MAX_ROWS:
            result.skipped += 1
            continue
        index = _line_number(number, len(block.lines))
        source_line = block.lines[index - 1] if index else find_source_line(block, name, value)
        result.rows.append(
            {
                "name": _COLUMN.sub(" ", name).strip(),
                "value_text": _COLUMN.sub(" ", value).strip(),
                "unit": _COLUMN.sub(" ", unit).strip(),
                "reference_text": "\n".join(
                    cleaned
                    for part in _REFERENCE_BREAK.split(reference)
                    if (cleaned := _clean(part))
                ),
                "printed_flag": "",
                "is_calculated": calc.strip().lower() in _TRUE,
                "source_line": source_line,
            }
        )
    return result


def parse_compact_rows(text: str, block: Block) -> list[dict]:
    return parse_compact(text, block).rows


def _clean(text: str) -> str:
    return " ".join(_COLUMN.sub(" ", text).split())


def parse_header_lines(text: str) -> dict:
    """Sete linhas `chave: valor` -> dict com as chaves do cabeçalho (tolerante)."""
    header: dict[str, str] = {}
    for raw_line in (text or "").splitlines():
        match = _HEADER_LINE.match(raw_line.strip())
        if not match:
            continue
        field_name = HEADER_KEYS.get(match["key"].lower())
        if field_name is None or field_name in header:
            continue
        value = match["value"].strip().strip("*_`").strip()
        header[field_name] = "" if value in _EMPTY_VALUES else value
    return header
