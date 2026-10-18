"""Regras determinísticas do layout "Softl@b" (`extract_softlab_block`).

Um bloco é um exame (resultado na linha de título) ou um painel (hemograma, PSA, bilirrubinas...
com várias linhas de resultado). A referência impressa (`VALORES DE REFERÊNCIA:`) pertence à
linha de resultado imediatamente anterior; se mais de uma linha a precede (ou se não há linha
nenhuma entre o cabeçalho e o resultado), só se atribui quando não há dúvida:

- antes de qualquer resultado: vale para o primeiro (painel que abre com a referência);
- depois de um único resultado sem referência: vale para ele;
- depois de `X` e de `X(%)` (o mesmo exame em percentual): vale para `X`;
- qualquer outro caso: o bloco inteiro vai ao LLM (nunca se emite meia tabela).

O texto de referência vai inteiro para `reference_text`; a faixa só é lida (`parse_reference_text`)
quando é uma faixa simples ou um par homens/mulheres (nada de matrizes por idade, fase do ciclo...).
Linha em colunas com cara de resultado que não é lida sem dúvida derruba o bloco.
"""

import re
from dataclasses import dataclass, field

from .blocks import Block
from .layouts import is_reference_cell, read_result_cell, read_table_cells, table_row
from .numbers import NUMBER_PATTERN, parse_digits
from .reference import parse_reference
from .softlab_lines import (
    RowLine,
    is_closer,
    is_footnote,
    is_meta,
    is_percent_variant,
    is_sex_columns,
    is_title_only,
    looks_like_result,
    parse_previous,
    parse_row,
    reference_header,
    split_cells,
)
from .text import fold, normalize_name
from .units import is_count_unit, is_known_unit, normalize_unit

MAX_REFERENCE_LINES = 80
MAX_SIMPLE_REFERENCE_LINES = 3
QUALITATIVE_REFERENCES = frozenset({"negativo", "nao reagente", "ausente", "positivo", "reagente"})
_COLUMN = re.compile(r"\s*\|\s*")
_TABLE_NAME_TAIL = re.compile(r"[\s.]*:?\s*$")

_N = NUMBER_PATTERN
_RANGE_EXPR = rf"(?:de\s+)?{_N}\s*(?:a|-|–)\s*{_N}"
_BOUND_EXPR = (
    r"(?:<=|=<|≤|>=|=>|≥|<|>|menor\s+ou\s+igual\s+a|maior\s+ou\s+igual\s+a"
    r"|igual\s+ou\s+maior\s+que|igual\s+ou\s+superior\s+a|menor\s+que|maior\s+que|inferior\s+a"
    rf"|superior\s+a|acima\s+de|abaixo\s+de|ate)\s*{_N}"
)
_SIMPLE_LINE = re.compile(
    r"^(?:(?P<label>homens?|mulheres?|masculino|feminino|criancas?)(?:\s*\([^)]*\))?\s*:?\s*)?"
    rf"(?:{_RANGE_EXPR}|{_BOUND_EXPR})\s*(?P<unit>[^\s\d].*?)?\s*\.?$"
)
# `0,77 A 2.19 ng/100ml.COM MÉDIA DE 1.23 ng/100ml`: a média não é limite da faixa.
_AVERAGE_CLAUSE = re.compile(r"\.?\s*com\s+media\s+de\s.*$")
_COLON_BEFORE_NUMBER = re.compile(r"(?<=[^\d\s]):(?=[\d<>])")
# `1.234`: ponto de milhar ou decimal? Só em contagem por µL o ponto é sempre milhar.
_AMBIGUOUS_NUMBER = re.compile(r"(?<![\d.,])[1-9]\d{0,2}\.\d{3}(?![\d.,])")


class _Bail(Exception):
    """O bloco não é lido sem dúvida: vai ao LLM (nunca se emite meia tabela)."""


def extract_softlab_block(block: Block) -> list[dict] | None:
    """Resultados do bloco Softl@b lidos por regras, ou `None` (o bloco vai ao LLM)."""
    if block.layout != "softlab":
        return None
    try:
        rows = _Reader(block).read()
    except _Bail:
        return None
    return rows or None


def simple_reference(reference: str, unit: str) -> str:
    """Referência que pode virar faixa numérica sem adivinhar; "" para matrizes e prosa.

    Aceita uma linha com uma faixa (`3.2 A 4.8 g/dl`, `< 34 U/L`, `ATÉ 140,0 mg/dL`) ou um
    conjunto de linhas `HOMENS: ...` / `MULHERES: ...` (mais uma de `CRIANÇAS: ...`, que
    `applicable_bands` nunca escolhe). A unidade impressa na referência, se houver, tem de ser
    a do resultado, `1.234` (milhar ou decimal?) fora de contagem por µL não é lido, e os
    limites que `parse_reference` devolve têm de existir, inteiros, no texto impresso. Uma só
    palavra qualitativa (`NEGATIVO`) também é referência. O texto devolvido é o que vai para a
    leitura: espaço depois de `:` colado a número, sem a cláusula "COM MÉDIA DE...".
    """
    lines = [
        _COLON_BEFORE_NUMBER.sub(": ", _without_average(line))  # `HOMENS:14.5` -> `HOMENS: 14.5`
        for line in reference.splitlines()
        if line.strip()
    ]
    if not 1 <= len(lines) <= MAX_SIMPLE_REFERENCE_LINES:
        return ""
    if len(lines) == 1 and fold(lines[0]).strip(" .") in QUALITATIVE_REFERENCES:
        return lines[0]
    labels: list[str | None] = []
    for line in lines:
        match = _SIMPLE_LINE.match(fold(line).strip())
        if not match:
            return ""
        printed = (match["unit"] or "").rstrip(". ")
        if printed and (
            not is_known_unit(printed) or (unit and normalize_unit(printed) != normalize_unit(unit))
        ):
            return ""
        labels.append(match["label"])
    if len(lines) > 1 and not _is_sex_set(labels):
        return ""
    text = "\n".join(lines)
    if not is_count_unit(normalize_unit(unit)) and _AMBIGUOUS_NUMBER.search(text):
        return ""
    return text if _bands_match_text(text) else ""


def _without_average(line: str) -> str:
    match = _AVERAGE_CLAUSE.search(fold(line))
    return (line[: match.start()] if match else line).strip()


def _is_sex_set(labels: list[str | None]) -> bool:
    """Todas as linhas rotuladas: um homens e uma mulheres (e no máximo uma de crianças)."""
    groups = [_label_group(label) for label in labels if label]
    return (
        len(groups) == len(labels)
        and groups.count("male") == 1
        and groups.count("female") == 1
        and groups.count("child") <= 1
    )


def _label_group(word: str) -> str:
    if word in {"homem", "homens", "masculino"}:
        return "male"
    return "child" if word.startswith("crianca") else "female"


def _bands_match_text(text: str) -> bool:
    """Defesa contra leitura errada de `parse_reference` (ex.: `HOMENS:14.5 A 48.4`)."""
    printed = {parse_digits(token)[0] for token in re.findall(NUMBER_PATTERN, text)}
    bands = parse_reference(text)
    return bool(bands) and all(
        limit in printed for band in bands for limit in (band.low, band.high) if limit is not None
    )


def _clean(line: str) -> str:
    """Linha de referência como impressa, sem o separador de colunas do linearizador."""
    return " ".join(_COLUMN.sub(" ", line).split())


@dataclass
class _Reader:
    block: Block
    rows: list[dict] = field(default_factory=list)
    pending: list[dict] = field(default_factory=list)  # resultados ainda sem referência
    targets: list[dict] | None = None  # quem recebe a seção de referência aberta (None = 1º)
    leading: list[str] | None = None  # referência lida antes de qualquer resultado
    reference: list[str] = field(default_factory=list)
    in_reference: bool = False
    closed: bool = False
    sex_columns: bool = False
    section: str = ""
    calculated: bool = False

    def read(self) -> list[dict]:
        lines = self.block.lines
        self.calculated = "calcul" in fold(self.block.method)
        first = lines[0]
        parsed = parse_row(first)
        if parsed is not None:
            self._add(parsed, first)
        elif is_title_only(first):
            self.section = first.strip()
        else:
            raise _Bail  # o bloco não começa num título: continuação de outra página?
        for line in lines[1:]:
            if not self.closed:
                self._line(line)
        self._close_reference()
        return self._finish()

    # --- linhas ---------------------------------------------------------------------------

    def _line(self, line: str) -> None:
        folded = fold(line).strip()
        if not folded:
            return
        if is_closer(folded):
            self._close_reference()
            self.closed = True
            return
        previous = parse_previous(line)
        if previous is not None:
            self._close_reference()
            self._attach_previous(previous)
            return
        header = reference_header(line)
        if header is not None:
            self._open_reference(header)
            return
        if is_meta(folded) or is_footnote(line):
            self._close_reference()
            return
        if is_sex_columns(line):
            self._close_reference()
            self.sex_columns = True
            return

        cells = split_cells(line)
        parsed = parse_row(line)
        if parsed is not None and not (
            self.in_reference and (" | " not in line or parsed.has_comparator)
        ):
            self._close_reference()
            self._add(parsed, line)
            return
        if parsed is None and not self.in_reference:
            table = self._table_rows(cells, line)
            if table is not None:
                self.rows.extend(table)
                return
        cleaned = cells if self.in_reference else [_TABLE_NAME_TAIL.sub("", cells[0]), *cells[1:]]
        if looks_like_result(cleaned, in_reference=self.in_reference):
            raise _Bail
        if self.in_reference:
            self._append_reference(line)
        elif len(cells) >= 2 and "referencia" in fold(cells[-1]):
            self.section = cells[0]  # `SÉRIE VERMELHA | VALORES DE REFERÊNCIA`
            self.sex_columns = False
        elif is_title_only(line):
            self.section = line.strip()
            self.sex_columns = False

    # --- resultados -----------------------------------------------------------------------

    def _add(self, parsed: RowLine, line: str) -> None:
        name = " ".join(parsed.name.split())
        # `% HEMOGLOBINA A1C | 5.7`: a unidade está impressa no rótulo, não ao lado do valor.
        unit = parsed.unit or ("%" if name.startswith("%") and not parsed.qualitative else "")
        row = table_row(name, parsed.value, unit, "", line)
        row["section"] = self.section
        if self.leading is not None:
            row["reference_text"] = "\n".join(self.leading)
            self.leading = None
        else:
            self.pending.append(row)
        self.rows.append(row)

    def _table_rows(self, cells: list[str], line: str) -> list[dict] | None:
        """Linha de tabela do hemograma, já com a referência (uma ou duas colunas)."""
        if len(cells) < 3:
            return None
        name = _TABLE_NAME_TAIL.sub("", cells[0]).strip()
        if not name:
            return None
        cells = [name, *cells[1:]]
        sex_reference = self._sex_columns_row(cells, line)
        rows = sex_reference or read_table_cells(cells, line)
        for row in rows or []:
            row["section"] = self.section
        return rows

    def _sex_columns_row(self, cells: list[str], line: str) -> list[dict] | None:
        """`HEMÁCIAS | 4.84 milhões/ul | 4.7 - 6.1 | 4.2 - 5.4` sob `HOMENS | MULHERES`."""
        if not self.sex_columns or len(cells) != 4:
            return None
        result = read_result_cell(cells[1])
        male, female = cells[2], cells[3]
        if result is None or not (is_reference_cell(male) and is_reference_cell(female)):
            return None
        value, unit = result
        reference = f"HOMENS: {male}\nMULHERES: {female}"
        row = table_row(cells[0], value, unit, reference, line)
        same = fold(male) == fold(female)
        row["parse_reference_text"] = simple_reference(male if same else reference, unit)
        return [row]

    def _attach_previous(self, pairs: list) -> None:
        if not self.rows:
            return
        row = self.rows[-1]
        if len(self.rows) > 1 and is_percent_variant(row["name"], self.rows[-2]["name"]):
            raise _Bail  # os anteriores são do `X` ou do `X(%)`? Não dá para saber
        row["previous"] = [[day.isoformat(), value] for day, value in pairs]

    # --- referência -----------------------------------------------------------------------

    def _open_reference(self, text: str) -> None:
        if self.in_reference:
            self._append_reference(text)  # segundo cabeçalho na mesma seção: continua nela
            return
        if not self.pending:
            # Antes de qualquer resultado: vale para o primeiro. Com resultados que já têm
            # referência (`VALORES DE REFERÊNCIA ANTIGO:`), a seção é descartada.
            self.targets = None if not self.rows else []
        elif len(self.pending) == 1 or all(
            is_percent_variant(row["name"], self.pending[0]["name"]) for row in self.pending[1:]
        ):
            self.targets = [self.pending[0]]
        else:
            raise _Bail  # dois resultados esperam a referência: de qual é?
        self.pending = []
        self.in_reference = True
        self.reference = []
        self._append_reference(text)

    def _append_reference(self, line: str) -> None:
        text = _clean(line)
        if not text:
            return
        if len(self.reference) >= MAX_REFERENCE_LINES:
            raise _Bail
        self.reference.append(text)

    def _close_reference(self) -> None:
        if not self.in_reference:
            return
        if self.targets is None:
            self.leading = self.reference
        else:
            for row in self.targets:
                row["reference_text"] = "\n".join(self.reference)
        self.in_reference = False
        self.targets = None
        self.reference = []

    # --- saída ----------------------------------------------------------------------------

    def _finish(self) -> list[dict]:
        names = [normalize_name(row["name"]) for row in self.rows]
        if len(set(names)) != len(names):
            raise _Bail  # dois resultados com o mesmo nome: layout ambíguo
        for row in self.rows:
            row["strict_reference"] = True
            if "parse_reference_text" not in row:
                row["parse_reference_text"] = simple_reference(row["reference_text"], row["unit"])
            row["is_calculated"] = row["is_calculated"] or (self.calculated and len(self.rows) == 1)
        return self.rows
