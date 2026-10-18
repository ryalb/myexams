"""Camada determinística: blocos lidos sem LLM.

A maior parte dos laudos imprime cada exame como `NOME | VALOR UNIDADE` (ou, para
exames com mais de uma grandeza, como `RÓTULO: | VALOR UNIDADE`), seguido de linhas de
metadados e do texto de referência (`extract_inline_block`). Tabelas como o hemograma
(`NOME | VALOR | UNIDADE | REFERÊNCIA`, com duas colunas de resultado no leucograma) são
lidas por `extract_table_rows`. O LLM fica só com o que as regras não leem com segurança.

Há ainda o layout da "ficha" (`extract_ficha_block`): um exame por trecho, sem `Material:`, com
o cabeçalho de colunas `RESULTADO` / `VALORES DE REFERÊNCIA` e o resultado (ou tabela de
resultados rotulados, como o hemograma) logo abaixo.

As funções devolvem `None` sempre que houver dúvida: o bloco então segue para o LLM
(nunca se emite meia tabela). A referência é copiada do bloco por código, nunca gerada
pelo modelo.
"""

import re
from collections.abc import Collection, Sequence

from .blocks import Block, is_name_continuation, split_title
from .numbers import NUMBER_PATTERN, parse_number
from .text import fold, normalize_name
from .units import infer_unit, is_count_unit, is_known_unit, normalize_unit

_COMPARATOR = r"(?:<=|=<|>=|=>|≤|≥|<|>)"
_VALUE = rf"{_COMPARATOR}?\s*{NUMBER_PATTERN}"
_NUMBER_CELL = re.compile(rf"^{_VALUE}(?=\s|$)")

_TITLE_INLINE = re.compile(rf"^(?P<value>{_VALUE})\s*(?P<unit>\S.*?)$")
_TITLE_NO_BAR = re.compile(
    rf"^(?P<name>[^|:]*?[^\W\d_][^|:]*?)\s+(?P<value>{_VALUE})\s*(?P<unit>\S.*?)$"
)
_LABELLED = re.compile(
    rf"^(?P<label>[^|:]{{1,80}}?)\s*:\s*(?:\|\s*)?(?P<value>{_VALUE})\s*(?:\|\s*)?(?P<unit>\S.*?)$"
)

# Rótulos que nunca são um exame (comparados no texto dobrado, sem acentos).
_META_LABEL = re.compile(
    r"^(?:material|metodo|data da coleta|valor(?:es)?\s*de|nota|notas|anteriores?|observa|"
    r"liberad|referencia|exame realizado|aten[cç]ao|padrao)"
)
# Rótulos de faixa/categoria de referência: "Normal: 70 mg/dL" não é um resultado.
_QUALIFIER_LABEL = re.compile(
    r"\b(?:normal|desejavel|otimo|limitrofe|alto|baixo|elevado|homens?|mulheres?|masculino|"
    r"feminino|adultos?|"
    r"criancas?|gestantes?|idosos?|anos|meses|jejum|fase|risco|ideal|aceitavel|inferior|"
    r"superior|menor|maior)\b"
)

_META_LINE = re.compile(
    r"^(?:material|metodo|data da coleta|valor(?:es)?\s*de|nota|anteriores?|observa|liberad)"
)

# Fim da seção de referência (texto dobrado: sem acentos, minúsculo).
_REF_HEADER = re.compile(r"^\s*valor(?:es)?\s*de\s*referencias?\s*(?::|$)")
_REF_STOP = re.compile(
    r"^\s*(?:notas?\b|anteriores?\b|observa|referencias?\s*:|liberad|metodo|material|"
    r"data da coleta|atencao|exame realizado|padrao de referencia|valor(?:es)?\s*de\s*refer|"
    r"\*|\(\d+\)|-\s)"
)
MAX_REFERENCE_LINES = 12
# Linha de texto corrido (sem nenhum número) é nota, não faixa de referência.
_PROSE_MIN_CHARS = 45

_ANCHOR = re.compile(r"^material\s*[:|]")
_CALCULATED = re.compile(r"calcul|formula|estimad")
_COLUMN = re.compile(r"\s*\|\s*")


def _split_cells(line: str) -> list[str]:
    return [cell.strip() for cell in re.split(r"\s+\|\s+", line.strip())]


def _is_table_row(line: str) -> bool:
    if _META_LINE.match(fold(line).lstrip()):
        return False
    cells = _split_cells(line)
    return len(cells) >= 3 and any(_NUMBER_CELL.match(cell) for cell in cells[1:])


def looks_like_table(lines: Sequence[str]) -> bool:
    """Três ou mais linhas em colunas com célula numérica: tabela, não um exame só."""
    return sum(1 for line in lines if _is_table_row(line)) >= 3


def _is_unit(unit: str, *, strict: bool = False) -> bool:
    return is_known_unit(unit, strict=strict)


def _is_label(label: str) -> bool:
    text = label.strip()
    folded = fold(text)
    return (
        len(text) >= 2
        and any(ch.isalpha() for ch in text)
        and "..." not in text
        and not _META_LABEL.match(folded)
        and not _QUALIFIER_LABEL.search(folded)
    )


def _parse_labelled(line: str) -> tuple[str, str, str] | None:
    """`RÓTULO: | VALOR UNIDADE` / `RÓTULO : VALOR UNIDADE` -> (rótulo, valor, unidade)."""
    match = _LABELLED.match(line.strip())
    if not match or not _is_label(match["label"]) or not _is_unit(match["unit"]):
        return None
    return match["label"].strip(), match["value"].strip(), match["unit"].strip()


def _parse_title(line: str) -> tuple[str, str, str] | None:
    """Título com resultado: `NOME | VALOR UNIDADE` (ou `NOME VALOR UNIDADE`)."""
    text = line.strip()
    cells = _split_cells(text)
    if len(cells) == 2:
        name, rest = cells
        match = _TITLE_INLINE.match(rest)
        if ":" in name or not match or not any(ch.isalpha() for ch in name):
            return None
        if not _is_unit(match["unit"]):
            return None
        return name, match["value"].strip(), match["unit"].strip()
    if len(cells) == 1 and "|" not in text and ":" not in text:
        match = _TITLE_NO_BAR.match(text)
        if match and _is_unit(match["unit"], strict=True):
            return match["name"].strip(), match["value"].strip(), match["unit"].strip()
    return None


def _is_stop(folded: str, line: str) -> bool:
    return bool(_REF_STOP.match(folded)) or _parse_labelled(line) is not None


def _is_prose(line: str) -> bool:
    return not any(ch.isdigit() for ch in line) and len(line.strip()) >= _PROSE_MIN_CHARS


def _reference_span(lines: Sequence[str], header_index: int) -> int:
    """Índice (exclusivo) onde termina a seção de referência que começa em `header_index`."""
    end = header_index + 1
    while end < len(lines) and end - header_index < MAX_REFERENCE_LINES:
        line = lines[end]
        if _is_stop(fold(line), line) or _is_prose(line):
            break
        end += 1
    return end


def _header_indices(lines: Sequence[str]) -> list[int]:
    return [i for i, line in enumerate(lines) if _REF_HEADER.match(fold(line))]


def _clean(text: str) -> str:
    return " ".join(_COLUMN.sub(" ", text).split())


def reference_for(block: Block, row_line_index: int, other_rows: Collection[int] = ()) -> str:
    """Texto de referência impresso para a linha `row_line_index` do bloco ("" se não houver).

    A seção começa na primeira linha DEPOIS da linha do resultado que casa com
    `Valor(es) de Referência:` e vai até uma linha de parada (nota, anteriores, método,
    novo resultado rotulado...) ou 12 linhas. Se outro resultado vier antes dessa
    seção, ela é dele, não desta linha.
    """
    lines = block.lines
    header = next((i for i in _header_indices(lines) if i > row_line_index), None)
    if header is None:
        return ""
    if any(row_line_index < other <= header for other in other_rows):
        return ""
    folded = fold(lines[header])
    match = _REF_HEADER.match(folded)
    first = lines[header][match.end() :] if match else ""
    parts = [first, *lines[header + 1 : _reference_span(lines, header)]]
    return "\n".join(cleaned for part in parts if (cleaned := _clean(part)))


def _is_calculated(name: str, lines: Sequence[str], row_index: int, next_row: int | None) -> bool:
    if "calculad" in fold(name) or "estimad" in fold(name):
        return True
    stop = next_row if next_row is not None else len(lines)
    for line in lines[row_index + 1 : stop]:
        folded = fold(line).lstrip()
        if folded.startswith("metodo") and _CALCULATED.search(folded):
            return True
    return False


def extract_inline_block(block: Block) -> list[dict] | None:
    """Linhas do bloco lidas por regras, ou `None` se o bloco deve ir ao LLM."""
    lines = block.lines
    if not lines or looks_like_table(lines):
        return None
    if not any(_ANCHOR.match(fold(line)) for line in lines):
        return None  # bloco de reserva (sem âncora de exame): não dá para confiar nas regras

    in_reference: set[int] = set()
    for header in _header_indices(lines):
        in_reference.update(range(header, _reference_span(lines, header)))

    candidates: list[tuple[int, str, str, str]] = []  # (linha, nome, valor, unidade)
    title = _parse_title(lines[0])
    if title is not None:
        name, value, unit = title
        if len(lines) > 1 and is_name_continuation(lines[1]) and not _ANCHOR.match(fold(lines[1])):
            name = f"{name} {lines[1].strip()}"  # nome em duas linhas (a 2ª, logo abaixo)
        candidates.append((0, name, value, unit))
    for index, line in enumerate(lines):
        if index in in_reference or (index == 0 and title is not None):
            continue
        labelled = _parse_labelled(line)
        if labelled is not None:
            candidates.append((index, *labelled))
        elif index > 0 and _parse_title(line) is not None:
            return None  # outro "NOME | VALOR UNIDADE" fora do título: layout ambíguo

    if not candidates:
        return None
    names = [normalize_name(name) for _, name, _, _ in candidates]
    if len(set(names)) != len(names):
        return None  # dois candidatos para o mesmo exame

    indexes = [index for index, *_ in candidates]
    rows = []
    for position, (index, name, value, unit) in enumerate(candidates):
        next_row = indexes[position + 1] if position + 1 < len(indexes) else None
        rows.append(
            {
                "name": name,
                "value_text": value,
                "unit": unit,
                "reference_text": reference_for(block, index, indexes),
                "printed_flag": "",
                "is_calculated": _is_calculated(name, lines, index, next_row),
                "source_line": lines[index],
            }
        )
    return rows


# --- tabelas: NOME | VALOR | UNIDADE | REFERÊNCIA ---------------------------------------------

MAX_TABLE_CELLS = 6
PERCENT_MAX = 100
ABSOLUTE_SUFFIX = " (absoluto)"
PERCENT_UNIT_HINT = "%"
ABSOLUTE_UNIT_HINT = "/µL"

# Linhas que nunca são resultado numa tabela: metadados, comentários e notas.
_TABLE_SKIP = re.compile(
    r"^(?:material|metodo|data da coleta|valor(?:es)?\s*de|notas?\b|anteriores?|observa|obs\b|"
    r"liberad|exame realizado|atencao)"
)
_SECTION_COLUMN = re.compile(r"referencia|resultado|unidade|valor", re.IGNORECASE)
SECTION_MAX_CHARS = 40

_RESULT_CELL = re.compile(rf"^(?P<value>{_VALUE})(?:\s*(?P<unit>\S.*))?$")
_TWO_VALUES = re.compile(
    rf"^(?P<v1>{NUMBER_PATTERN})(?:\s*(?P<u1>%))?\s+(?P<v2>{NUMBER_PATTERN})"
    rf"(?:\s*(?P<u2>\S.*))?$"
)
_PLAIN_NUMBER_CELL = re.compile(rf"^{NUMBER_PATTERN}(?:\s*\S.*)?$")

_RANGE = re.compile(rf"{NUMBER_PATTERN}\s*(?:a|ate|-|–|—)\s*{NUMBER_PATTERN}")
_BOUND = re.compile(
    rf"(?:<=|=<|>=|=>|≤|≥|<|>|menor que|maior que|inferior a|superior a|ate|acima de|"
    rf"abaixo de)\s*{NUMBER_PATTERN}"
)


def _is_reference_cell(cell: str) -> bool:
    """A célula traz uma expressão de faixa (`A a B`, `< X`, `> X`...)."""
    folded = fold(cell)
    return bool(_RANGE.search(folded) or _BOUND.search(folded))


def _is_section_line(line: str) -> bool:
    """`ERITROGRAMA` / `LEUCOGRAMA | Valores de referência`: título de seção da tabela."""
    head, sep, rest = line.partition(" | ")
    head = head.strip()
    if not head or len(head) > SECTION_MAX_CHARS or any(ch.isdigit() for ch in head):
        return False
    if not any(ch.isalpha() for ch in head) or head != head.upper():
        return False
    if sep:
        return (
            not any(ch.isdigit() for ch in rest) and _SECTION_COLUMN.search(fold(rest)) is not None
        )
    return True


def _is_column_units(cells: Sequence[str]) -> bool:
    """`% | /µL | % | /µL`: linha só de unidades que rotula as colunas de resultado."""
    return (
        len(cells) >= 2
        and not any(ch.isdigit() for cell in cells for ch in cell)
        and all(_is_unit(cell) for cell in cells)
    )


def _result_cell(cell: str) -> tuple[str, str] | None:
    """`4,85` / `4,85 milhões/uL` / `< 0,5 mg/dL` -> (valor impresso, unidade impressa)."""
    match = _RESULT_CELL.match(cell.strip())
    if not match:
        return None
    unit = (match["unit"] or "").strip()
    if unit and not _is_unit(unit):
        return None
    return match["value"].strip(), unit


def _belongs_to_absolute(reference: str) -> bool | None:
    """Com uma única referência numa linha de dois resultados, de qual coluna ela é?

    Pela unidade impressa; sem unidade: limite acima de 100 só cabe em contagem, e limites
    com casa decimal (`0,0 a 5,0`) são de percentual — contagens saem como números inteiros.
    `None` = indeterminado (sem unidade, só inteiros pequenos, ex.: `< 100`): quem chama
    devolve o bloco ao LLM em vez de adivinhar a coluna.
    """
    unit = infer_unit(reference)
    if unit == "%":
        return False
    if is_count_unit(unit):
        return True
    decimal_seen = False
    for number in re.findall(NUMBER_PATTERN, reference):
        value = parse_number(number).value
        if value is None:
            continue
        if value > PERCENT_MAX:
            return True
        decimal_seen = (
            decimal_seen or "," in number or ("." in number and not _is_thousands(number))
        )
    return False if decimal_seen else None


def _is_thousands(number: str) -> bool:
    """`2.000` / `450.000` (ponto de milhar) em oposição a `1.5` (decimal)."""
    return bool(re.fullmatch(r"\d{1,3}(\.\d{3})+", number))


def _table_row(name: str, value: str, unit: str, reference: str, line: str, **extra) -> dict:
    lowered = fold(name)
    return {
        "name": name,
        "value_text": value,
        "unit": unit,
        "reference_text": reference,
        "printed_flag": "",
        "is_calculated": "calculad" in lowered or "estimad" in lowered,
        "source_line": line,
        **extra,
    }


def _two_result_rows(
    name: str,
    first: tuple[str, str],
    second: tuple[str, str],
    references: Sequence[str],
    column_units: tuple[str, str],
    line: str,
) -> list[dict] | None:
    """`NOME | % | absoluto | ref% | ref abs`: duas linhas, a segunda com `(absoluto)`."""
    (v1, u1), (v2, u2) = first, second
    unit1, unit2 = u1 or column_units[0], u2 or column_units[1]
    if unit1 and normalize_unit(unit1) != "%":
        return None  # duas grandezas que não são percentual + absoluto
    if unit2 and not is_count_unit(normalize_unit(unit2)):
        return None
    if len(references) > 2 or not all(_is_reference_cell(ref) for ref in references):
        return None
    percent = parse_number(v1).value
    if percent is None or percent > PERCENT_MAX:
        return None  # "percentual" acima de 100: não é percentual

    ref1 = ref2 = ""
    if len(references) == 2:
        ref1, ref2 = references
    elif len(references) == 1:
        side = _belongs_to_absolute(references[0])
        if side is None:
            return None  # não dá para saber a coluna da única referência: não chutar
        if side:
            ref2 = references[0]
        else:
            ref1 = references[0]
    return [
        _table_row(name, v1, unit1, ref1, line, unit_hint="" if unit1 else PERCENT_UNIT_HINT),
        _table_row(
            f"{name}{ABSOLUTE_SUFFIX}",
            v2,
            unit2,
            ref2,
            line,
            unit_hint="" if unit2 else ABSOLUTE_UNIT_HINT,
        ),
    ]


def _parse_table_line(
    cells: Sequence[str], column_units: tuple[str, str], line: str
) -> list[dict] | None:
    if not 2 <= len(cells) <= MAX_TABLE_CELLS or not _is_label(cells[0]):
        return None
    name, rest = cells[0], list(cells[1:])

    joined = _TWO_VALUES.match(rest[0])
    if joined:  # `Neutrófilos | 60,0 2.400 | 40,0 a 75,0 | 2.000 a 7.000`
        u1, u2 = joined["u1"] or "", (joined["u2"] or "").strip()
        if u2 and not _is_unit(u2):
            return None
        return _two_result_rows(
            name, (joined["v1"], u1), (joined["v2"], u2), rest[1:], column_units, line
        )

    first = _result_cell(rest[0])
    if first is None:
        return None  # valor não numérico (qualitativo ou texto): não é tabela numérica
    if len(rest) > 1 and _PLAIN_NUMBER_CELL.match(rest[1]) and not _is_reference_cell(rest[1]):
        second = _result_cell(rest[1])
        if second is not None:  # `Neutrófilos | 60,0 | 2.400 | 40,0 a 75,0 | 2.000 a 7.000`
            return _two_result_rows(name, first, second, rest[2:], column_units, line)

    value, unit = first
    references = rest[1:]
    if not unit:
        if not references or not _is_unit(references[0]):
            return None  # sem unidade reconhecida
        unit, references = references[0], references[1:]
    if not all(_is_reference_cell(ref) for ref in references):
        return None
    return [_table_row(name, value, unit, " ".join(references), line)]


def extract_table_rows(block: Block) -> list[dict] | None:
    """Tabela de resultados lida por regras, ou `None` se o bloco deve ir ao LLM.

    Só vale se TODA linha em colunas (` | `) que não seja seção, comentário ou metadado
    for lida sem dúvida: nunca se emite meia tabela. Linhas de duas colunas de resultado
    (`percentual + absoluto`) viram duas linhas, a segunda com ` (absoluto)` no nome e com
    `unit_hint` quando a unidade não vem impressa na linha nem no cabeçalho de colunas.
    """
    lines = block.lines
    if not lines or not any(_ANCHOR.match(fold(line)) for line in lines):
        return None  # bloco de reserva (sem âncora de exame): não dá para confiar nas regras
    if _header_indices(lines):
        return None  # referência em seção própria (`Valor de Referência:`): layout de linha única

    rows: list[dict] = []
    column_units = ("", "")
    for index, line in enumerate(lines):
        if _TABLE_SKIP.match(fold(line).lstrip()):
            continue
        if _is_section_line(line):
            column_units = ("", "")
            continue
        if " | " not in line:
            if _parse_title(line) is not None:
                return None  # resultado fora do formato de colunas: layout ambíguo
            continue
        cells = _split_cells(line)
        if _is_column_units(cells):
            column_units = (cells[0], cells[1])
            continue
        parsed = _parse_table_line(cells, column_units, line)
        if index == 0:
            # Título do bloco: sem valor é só o nome do exame/painel. Com valor ("PERFIL | 5 g/dL")
            # o layout é ambíguo (painel ou resultado?): vai ao LLM em vez de virar resultado falso.
            if parsed is not None:
                return None
            continue
        if parsed is None:
            return None
        rows.extend(parsed)
    return rows or None


# --- layout "ficha": RESULTADO / VALORES DE REFERÊNCIA ----------------------------------------
#
# Um exame por bloco, em uma destas formas (todas com `None` se algo não for lido sem dúvida):
#   1. resultado único: `143 mg/dL | <referência, possivelmente em várias linhas>`, com o nome
#      vindo do título (ou da seção `PLAQUETAS ====` quando o bloco é uma tabela);
#   2. resultados rotulados: `PSA Total: 0,57 ng/mL | vide nota 1`, `HEMOGLOBINA : 15,1 g/dL | ...`
#      (inclusive nomes quebrados em duas linhas e o leucograma com percentual + absoluto).
# A referência impressa é sempre mantida como veio (`reference_text`); a faixa numérica só é lida
# (`parse_reference_text`) quando o texto não for uma matriz por idade/sexo nem prosa.

MAX_PARSEABLE_REFERENCE_CHARS = 220
GENERIC_LABELS = frozenset({"total", "direta", "indireta", "livre"})
MAX_NAME_STUBS = 2
MAX_NAME_CHARS = 90

_NUM = NUMBER_PATTERN
_CMP_PHRASE = (
    r"(?:<=|=<|>=|=>|≤|≥|<|>|menor ou igual a|maior ou igual a|menor que|maior que|inferior a|"
    r"superior a|abaixo de|acima de)"
)
_FICHA_VALUE = re.compile(rf"^(?P<value>(?:{_CMP_PHRASE}\s*)?{_NUM})(?P<tail>.*)$", re.IGNORECASE)
_FICHA_LABELLED = re.compile(r"^(?P<label>[^\d:|][^:]*?)\s*:\s*(?P<rest>\S.*)$")
_FICHA_UNLABELLED = re.compile(rf"^(?P<label>[^\d:]*[^\W\d_][^\d:]*?)\s+(?P<rest>{_NUM}.*)$")
_ROWISH = re.compile(rf"^[^\d:|][^:]*:\s*(?:{_CMP_PHRASE}\s*)?\d", re.IGNORECASE)
_ROWISH_UNLABELLED = re.compile(r"^[^\d:]*[^\W\d_][^\d:]*\s\d")
# Depois de um "valor" de verdade nunca vem "a 65" (faixa) nem "anos" (idade): `Acima de 12 anos:`
# e `18 a 65 anos` são qualificadores da referência, não resultados.
_NOT_A_RESULT_TAIL = re.compile(
    rf"^\s*(?:(?:a|ate|-|–|—)\s*{_NUM}|(?:anos?|meses|mes|dias?|semanas?)\b|a\s*:)"
)
_AGE_QUALIFIER_LINE = re.compile(
    r"^(?:(?:acima|abaixo) de|(?:maior|menor)(?:es)? (?:ou igual a|que|de)|a partir de|ate|"
    r"mais de|de)?"
    r"\s*\d+\s*(?:a\s*\d+\s*)?(?:anos?|meses)\s*:?$"
)
_RANGE_START = re.compile(rf"^{_NUM}\s*(?:a|ate|-|–|—)\s*{_NUM}(?:\s|$)", re.IGNORECASE)
_TWO_VALUES_RANGE = re.compile(rf"^(?P<v2>{_NUM})\s+(?P<ref>{_NUM}\s*(?:a|ate|-|–|—)\s*{_NUM}.*)$")

_RESULT_HEADER = re.compile(r"^resultado(?!\w)")
_REFERENCE_HEADER = re.compile(r"^valor(?:es)?\s+de\s+referencia")
_HEADER_BOTH = re.compile(r"^resultado\s+valor(?:es)?\s+de\s+referencia(?:\s*\(.*\))?:?$")
_HEADER_REFERENCE = re.compile(r"^valor(?:es)?\s+de\s+referencia(?:\s*\(.*\))?:?$")
_POPULATION = re.compile(r"\b(?:masc|fem|homens?|mulheres?|masculino|feminino|anos?|adultos?)\b")
_SECTION_MARK = re.compile(r"^(?P<name>[^=|]*[^\W\d_][^=|]*?)\s*\|?\s*={5,}\s*$")
_SEPARATOR = re.compile(r"^[-+=|\s]{5,}$")
_MORPHOLOGY = re.compile(r"^caracteres\s+morfologicos\s*:?$")
_FICHA_STOP = re.compile(
    r"^(?:\*|nota|ref\.|referencias?\s*:|padrao de referencia|calculo baseado|recebido|"
    r"resultados anteriores|ensaio ultra|limite de|alteracao de metodo|exame realizado|atencao)"
)
_MATRIX = re.compile(
    r"\b(?:masculino|feminino|homens?|mulheres?|masc|fem)\s*:?\s*"
    r"(?:masculino|feminino|homens?|mulheres?|masc|fem)\b|\bidade\s*:|trimestre|"
    rf"{_NUM}\s*(?:a|-|–|—)\s*{_NUM}\s+{_NUM}\s*(?:a|-|–|—)\s*{_NUM}"
)
_CALCULATED_META = re.compile(r"^formula|^metodo.*(?:calcul|formula)")


class _Bail(Exception):
    """O bloco não é lido sem dúvida: vai ao LLM (nunca se emite meia tabela)."""


def _is_header(text: str, key: str) -> bool:
    """Linha de cabeçalho de colunas: começa em CAIXA ALTA (`RESULTADO ...`, `VALORES DE ...`).

    Frases de nota que começam por "resultado" ou "valores de referência" em minúsculas
    ("resultado deve ser avaliado...") continuam sendo prosa.
    """
    first = text.split(" ", 1)[0]
    return first.isupper() and bool(_RESULT_HEADER.match(key) or _REFERENCE_HEADER.match(key))


def _value_match(text: str) -> re.Match[str] | None:
    """`143 mg/dL ...` / `inferior a 0,10 mEq/L` -> valor + cauda; `None` se não for um valor."""
    match = _FICHA_VALUE.match(text)
    if match and _NOT_A_RESULT_TAIL.match(fold(match["tail"])):
        return None
    return match


def _flat(line: str) -> str:
    """Linha sem o separador de colunas do linearizador (as colunas variam com o vão)."""
    return " ".join(re.sub(r"\s*\|\s*", " ", line.replace("\u00ad", "-")).split())


def _header_at(flat: Sequence[str], folded: Sequence[str], index: int) -> tuple[int, str, str]:
    """Cabeçalho de colunas em `index` -> (linhas consumidas, tipo, população).

    Tipos: `both` (resultado e referência juntos), `result` (só `RESULTADO`) e `reference`
    (só `VALORES DE REFERÊNCIA`). A população (`Masc: Maior ou igual a 18 anos`) vem na linha
    do resultado quando a ordem é trocada. Qualquer outra variante (ex.: `RESULTADO | AMOSTRA`)
    é desconhecida.
    """
    line = folded[index]
    if line == "resultado":
        return 1, "result", ""
    if _HEADER_BOTH.match(line):
        return 1, "both", ""
    if _HEADER_REFERENCE.match(line):
        following = folded[index + 1] if index + 1 < len(folded) else ""
        if following.startswith("resultado "):
            population = flat[index + 1][len("RESULTADO") :].strip()
            if _POPULATION.search(fold(population)):
                return 2, "both", population
            raise _Bail
        return 1, "reference", ""
    raise _Bail


def _take_unit(tail: str) -> tuple[str, str]:
    token, _, rest = tail.strip().partition(" ")
    if token and _is_unit(token):
        return token, rest.strip()
    return "", tail.strip()


def _reference_with_population(reference: str, population: str) -> str:
    if not reference or not population:
        return reference
    return f"{population.rstrip(':')}: {reference}"


def _parseable_reference(reference: str) -> str:
    """Referência que pode virar faixa numérica sem adivinhar; "" se for matriz ou prosa."""
    if not reference or len(reference) > MAX_PARSEABLE_REFERENCE_CHARS:
        return ""
    return "" if _MATRIX.search(fold(reference)) else reference


def _ficha_row(
    name: str,
    value: str,
    unit: str,
    reference: str,
    line: str,
    *,
    section: str,
    population: str,
    calculated: bool,
) -> dict:
    full_reference = _reference_with_population(reference, population)
    row = _table_row(name, value, unit, full_reference, line)
    row["is_calculated"] = row["is_calculated"] or calculated
    row["section"] = section
    row["strict_reference"] = True
    row["parse_reference_text"] = _parseable_reference(full_reference)
    return row


def _labelled_name(label: str, stubs: list[str], title_name: str) -> str:
    base = " ".join([*stubs, label.strip()])
    if not stubs and fold(base) in GENERIC_LABELS:
        base = f"{title_name} {base}"  # `DIRETA` sozinho não diz de que exame é
    return base


def _leukogram_values(
    tail: str, units: Sequence[str]
) -> tuple[tuple[str, str], tuple[str, str] | None, str]:
    """Cauda de `Neutrófilos : 52,7 2.930 1.590 a 4.770` sob o cabeçalho de unidades.

    Devolve (1º valor, 2º valor ou None, referência). Sem referência legível, levanta _Bail.
    """
    first = _value_match(tail)
    if not first:
        raise _Bail
    value1, rest = first["value"], first["tail"].strip()
    if _RANGE_START.match(rest):
        return (value1, ""), None, rest
    two = _TWO_VALUES_RANGE.match(rest)
    if not two:
        raise _Bail
    return (value1, ""), (two["v2"], ""), two["ref"].strip()


def _leukogram_rows(
    name: str,
    tail: str,
    units: Sequence[str],
    line: str,
    **context,
) -> list[dict]:
    first, second, reference = _leukogram_values(tail, units)
    count_reference = len(units) >= 3 and is_count_unit(normalize_unit(units[2]))
    if second is None:
        side = _belongs_to_absolute(reference)
        if side is None:
            raise _Bail  # uma só coluna de resultado e coluna indecidível: não chutar
        unit = units[1] if side and len(units) > 1 else units[0]
        return [_ficha_row(name, first[0], unit, reference, line, **context)]
    if normalize_unit(units[0]) != "%" or not is_count_unit(normalize_unit(units[1])):
        raise _Bail
    percent = parse_number(first[0]).value
    if percent is None or percent > PERCENT_MAX:
        raise _Bail
    absolute_reference = count_reference or _belongs_to_absolute(reference)
    if absolute_reference is None:
        raise _Bail
    pct_ref, abs_ref = ("", reference) if absolute_reference else (reference, "")
    return [
        _ficha_row(name, first[0], units[0], pct_ref, line, **context),
        _ficha_row(f"{name}{ABSOLUTE_SUFFIX}", second[0], units[1], abs_ref, line, **context),
    ]


def extract_ficha_block(block: Block) -> list[dict] | None:
    """Resultados do bloco do layout ficha lidos por regras, ou `None` (vai ao LLM).

    Máquina de estados sobre as linhas: cabeçalho de colunas -> primeiro resultado (único,
    nomeado pelo título/seção, ou rotulado) -> referência/linhas seguintes -> notas. Qualquer
    linha com cara de resultado que não seja lida sem dúvida derruba o bloco inteiro.
    """
    if block.layout != "ficha":
        return None
    try:
        rows = _read_ficha(block)
    except _Bail:
        return None
    return rows or None


def _read_ficha(block: Block) -> list[dict]:
    lines = block.lines
    flat = [_flat(line) for line in lines]
    folded = [fold(f) for f in flat]
    first = next(
        (i for i, f in enumerate(folded) if _is_header(flat[i], f)),
        None,
    )
    if first is None or first == 0:
        raise _Bail
    title_name = split_title(flat[0])[0]
    calculated = "calculad" in fold(title_name) or any(
        _CALCULATED_META.match(f) for f in folded[:first]
    )

    rows: list[dict] = []
    section = ""
    units: tuple[str, ...] = ()
    population = ""
    has_reference_column = False
    mode = "head"  # head | await | rows | ref | tail
    group_rows = 0
    stubs: list[str] = []
    closed = False
    morphology = False
    # Resultado único à espera da referência: (linha da referência impressa na própria linha).
    single: dict | None = None
    reference_lines: list[str] = []
    reference_open = False

    def finish_single() -> None:
        nonlocal single, reference_lines, reference_open
        if single is not None:
            reference = " ".join(reference_lines).strip() if reference_open else ""
            full = _reference_with_population(reference, population)
            single["reference_text"] = full
            single["parse_reference_text"] = _parseable_reference(full)
        single, reference_lines, reference_open = None, [], False

    index = first
    while index < len(lines):
        text, key = flat[index], folded[index]
        if not text:
            index += 1
            continue

        if _is_header(text, key):
            consumed, kind, header_population = _header_at(flat, folded, index)
            if mode == "ref" and kind == "reference" and single is not None and not reference_open:
                reference_open, reference_lines = True, []  # a referência vem depois do valor
                index += consumed
                continue
            if mode in ("ref", "tail") or kind == "reference":
                raise _Bail  # outra seção de resultados: não é um exame de um resultado só
            finish_single()
            mode, group_rows, stubs, closed, morphology, units = "await", 0, [], False, False, ()
            population, has_reference_column = header_population, kind == "both"
            index += consumed
            continue

        section_mark = _SECTION_MARK.match(text)
        if section_mark:
            if mode in ("ref", "tail"):
                raise _Bail
            section = section_mark["name"].strip()
            mode, group_rows, stubs, closed, morphology, units = "await", 0, [], False, False, ()
            index += 1
            continue

        if _SEPARATOR.match(text):
            index += 1
            continue

        if _FICHA_STOP.match(key):
            if mode == "ref":
                finish_single()
            mode, morphology = "tail", False
            index += 1
            continue

        if mode == "tail":
            index += 1
            continue

        if mode == "ref":
            if reference_open and len(reference_lines) < MAX_REFERENCE_LINES:
                reference_lines.append(text)
            index += 1
            continue

        if _MORPHOLOGY.match(key):
            morphology = True
            index += 1
            continue
        if morphology:
            index += 1
            continue

        if mode == "await" and group_rows == 0 and _AGE_QUALIFIER_LINE.match(key):
            population = f"{population} {text}".strip()  # `Acima de 12 anos:` vale p/ as linhas
            index += 1
            continue

        tokens = text.split()
        if len(tokens) >= 2 and all(_is_unit(token) for token in tokens):
            units = tuple(tokens)
            index += 1
            continue

        value_line = _value_match(text)
        if value_line and mode == "await" and group_rows == 0 and not units:
            unit, remainder = _take_unit(value_line["tail"])
            name = section or title_name
            single = _ficha_row(
                name, value_line["value"], unit, "", lines[index],
                section=section, population=population, calculated=calculated,
            )  # fmt: skip
            rows.append(single)
            group_rows += 1
            reference_open = has_reference_column
            reference_lines = [remainder] if remainder else []
            mode = "ref"
            index += 1
            continue

        parsed = _labelled_row(text, units, stubs, title_name)
        if parsed is not None:
            if closed:
                raise _Bail  # linha de resultado depois de prosa: tabela ambígua
            name, tail, via_units = parsed
            context = {
                "section": section, "population": population, "calculated": calculated,
            }  # fmt: skip
            if via_units:
                rows.extend(_leukogram_rows(name, tail, units, lines[index], **context))
            else:
                value = _value_match(tail)
                if not value:
                    raise _Bail
                unit, reference = _take_unit(value["tail"])
                rows.append(
                    _ficha_row(name, value["value"], unit, reference, lines[index], **context)
                )
            stubs, group_rows, mode = [], group_rows + 1, "rows"
            index += 1
            continue

        if _ROWISH.match(text) or (units and _ROWISH_UNLABELLED.match(text)):
            raise _Bail  # parece resultado e não foi lido

        if _is_name_stub(text, key) and len(stubs) < MAX_NAME_STUBS and not closed:
            stubs.append(text)
            index += 1
            continue

        if mode == "await" and group_rows == 0:
            raise _Bail  # texto desconhecido antes do primeiro resultado
        closed = True
        stubs = []
        index += 1

    finish_single()
    return rows


def _is_name_stub(text: str, key: str) -> bool:
    """Linha só de nome em CAIXA ALTA, sem número: primeira metade de um nome quebrado em duas."""
    return (
        len(text) >= 3
        and not any(ch.isdigit() for ch in text)
        and text == text.upper()
        and not _FICHA_STOP.match(key)
    )


def _labelled_row(
    text: str, units: Sequence[str], stubs: list[str], title_name: str
) -> tuple[str, str, bool] | None:
    """`RÓTULO : VALOR ...` (ou, sob cabeçalho de unidades, `RÓTULO VALOR ...`).

    Devolve (nome, cauda depois do rótulo, se há cabeçalho de unidades).
    """
    labelled = _FICHA_LABELLED.match(text)
    value = _value_match(labelled["rest"]) if labelled else None
    if labelled and _is_label(labelled["label"]) and value:
        name = _labelled_name(labelled["label"], stubs, title_name)
        if len(name) > MAX_NAME_CHARS:
            raise _Bail
        return name, labelled["rest"], bool(units)
    if units and not labelled:
        bare = _FICHA_UNLABELLED.match(text)
        if bare and _is_label(bare["label"]):
            name = _labelled_name(bare["label"], stubs, title_name)
            if len(name) > MAX_NAME_CHARS:
                raise _Bail
            return name, bare["rest"], True
    return None


# --- API para outros layouts (Softl@b) ---------------------------------------------------------


def read_table_cells(cells: Sequence[str], line: str) -> list[dict] | None:
    """Linha de tabela já separada em células -> linhas de resultado (ou `None`).

    `Neutrófilos | 62 % | 3.472 / mm3 | 55 - 65 | 2.310 - 7.150` vira percentual + absoluto.
    """
    return _parse_table_line(cells, ("", ""), line)


def is_reference_cell(cell: str) -> bool:
    return _is_reference_cell(cell)


def read_result_cell(cell: str) -> tuple[str, str] | None:
    return _result_cell(cell)


def table_row(name: str, value: str, unit: str, reference: str, line: str) -> dict:
    return _table_row(name, value, unit, reference, line)
