"""Gramática de linhas do layout "Softl@b" (cabeçalho `Pedido:` + `Pag.: n de m`).

Cada exame é um trecho que começa numa linha de título com o resultado (`NOME | VALOR UNIDADE`,
`NOME | VALOR | UNIDADE`, ponto como separador decimal) ou, nos painéis (hemograma, PSA...), numa
linha só com o nome, seguida de `MÉTODO:`/`MATERIAL:`, linhas de resultado, `VALORES DE
REFERÊNCIA:` e `DATA DA COLETA:`. Este módulo só reconhece a forma das linhas; quem agrupa
(`blocks`) e quem lê (`softlab`) usam as mesmas definições, para nunca discordarem.

Sem dependência de `blocks`/`layouts` (os dois importam este módulo).
"""

import re
from dataclasses import dataclass
from datetime import date

from .numbers import NUMBER_PATTERN
from .text import fold, normalize_name
from .units import is_known_unit

_CMP = r"(?:<=|=<|>=|=>|≤|≥|<|>)"
_VALUE = rf"{_CMP}?\s*{NUMBER_PATTERN}"
_CELL_SPLIT = re.compile(r"\s+\|\s+")
_VALUE_UNIT = re.compile(rf"^(?P<value>{_VALUE})\s*(?P<unit>[^\d\s<>].*?)?\s*$")
_PLAIN_VALUE = re.compile(rf"^{_VALUE}$")
_SINGLE = re.compile(
    rf"^(?P<name>\S.*?)\s+(?P<value>{_VALUE})\s*(?P<unit>[^\d\s<>]\S*(?:\s\S+)?)\s*$"
)
_NUMBER_START = re.compile(rf"^\s*(?:{_CMP}\s*)?\d")
_COMPARATOR = re.compile(rf"^\s*{_CMP}")
_LOWER_RUN = re.compile(r"[a-zà-öø-ÿ]{3,}")
_PARENTHESES = re.compile(r"\([^)]*\)")
_CODE_PREFIX = re.compile(r"^\d+\s*-\s*[^\W\d_]")
_RANGE_CELL = re.compile(rf"^{NUMBER_PATTERN}\s*(?:a|à|até|ate|-|–)\s*{NUMBER_PATTERN}", re.I)

# Sem coluna (`NOME 4.5 g/dl`) uma frase de referência ou de nota pode parecer um resultado
# (`ESSES VALORES SITUAM-SE ENTRE 30.0 E 90.0 ng/ml`): nome com número decimal, muito longo ou
# com estas palavras não vale. Em colunas (o que o laboratório imprime) não há essa restrição.
_PROSE_WORDS = re.compile(
    r"\b(?:entre|maior|menor|igual|ate|acima|abaixo|superior|inferior|valores?|ideal|desejavel)\b"
)
_DECIMAL_TOKEN = re.compile(r"\d[.,]\d")
MAX_UNPIPED_NAME_WORDS = 6

# Resultado qualitativo: só um vocabulário fechado (qualquer outro texto vai ao LLM).
_QUALITATIVE = frozenset(
    {"negativo", "positivo", "reagente", "nao reagente", "ausente", "presente", "normal"}
)


def is_plain_value(text: str) -> bool:
    """Só um número impresso, com comparador opcional (`4,5`, `<0,5`, `1.234`)."""
    return _PLAIN_VALUE.match(text.strip()) is not None


def is_qualitative_text(text: str) -> bool:
    """O texto está no vocabulário qualitativo fechado (`Negativo`, `Não reagente`...)."""
    return fold(text).strip(" .") in _QUALITATIVE


# Linhas que não são resultado nem referência (texto dobrado: minúsculo e sem acento).
_META = re.compile(
    r"^(?:metodos?\b|material\b|registro\s+anvisa|data\s+(?:da\s+)?(?:coleta|liberacao)"
    r"|resultados?\s+anteriores|obs\b|nota\b|atencao|exame\s+realizado|jejum\s*:|\(metodo"
    r"|novos?\s+valores|nova\s+metodologia|fonte|-\s*fonte|\[@\])"
)
_REFERENCE_HEADER = re.compile(r"^valor(?:es)?\s*(?:de\s*)?ref\w*\s*\*?\s*:?\s*")
_CLOSER = re.compile(r"^data\s+(?:da\s+)?(?:coleta|liberacao)")
_METHOD_OR_MATERIAL = re.compile(r"^(?:metodos?|material)\s*[:|]")
_PREVIOUS = re.compile(r"^resultados?\s+anteriores\s*:")
_PREVIOUS_PAIR = re.compile(rf"(?P<value>{_VALUE})\s*\[(?P<date>\d{{2}}/\d{{2}}/\d{{4}})\]")
_SEX_COLUMNS = re.compile(r"^(?:homens?\s*\|\s*mulheres?)$")


@dataclass(frozen=True)
class RowLine:
    """Resultado lido de uma linha: nome, valor impresso, unidade impressa (ou texto)."""

    name: str
    value: str
    unit: str = ""
    qualitative: bool = False

    @property
    def strong(self) -> bool:
        """Com unidade (ou qualitativo): pode abrir um exame. Só número é linha de painel."""
        return bool(self.unit) or self.qualitative

    @property
    def has_comparator(self) -> bool:
        return bool(_COMPARATOR.match(self.value))


def split_cells(line: str) -> list[str]:
    return [cell.strip() for cell in _CELL_SPLIT.split(line.strip())]


def has_lowercase_word(text: str) -> bool:
    """Palavra em minúsculas (3+ letras) fora de parênteses: texto corrido, não um nome."""
    return _LOWER_RUN.search(_PARENTHESES.sub("", text)) is not None


def is_name(name: str) -> bool:
    """Nome de exame/analito: CAIXA ALTA, sem `:` nem pontilhado; `25 - HIDROXI...` vale."""
    text = name.strip()
    if not 2 <= len(text) <= 100 or ":" in text or "..." in text:
        return False
    if not (text[0].isalpha() or text[0] == "%" or _CODE_PREFIX.match(text)):
        return False
    return any(ch.isalpha() for ch in text) and not has_lowercase_word(text)


def _join_name(cells: list[str]) -> str:
    """Nome em uma ou mais células (`IGFBP - 3 | PROTEINA...`, `NOME | (sigla)`)."""
    if not cells or not is_name(cells[0]):
        return ""
    for extra in cells[1:]:
        if not extra or _NUMBER_START.match(extra) or not any(ch.isalpha() for ch in extra):
            return ""
    name = " ".join(cells)
    return name if is_name(name) else ""


def _from_cells(cells: list[str]) -> RowLine | None:
    last = cells[-1]
    head = cells[:-1]
    if fold(last).strip(" .") in _QUALITATIVE:
        name = _join_name(head)
        return RowLine(name, last.strip(" ."), "", True) if name else None

    match = _VALUE_UNIT.match(last)
    value = unit = ""
    if match:
        value = match["value"].strip()
        unit = (match["unit"] or "").strip().rstrip(".").strip()
        if unit and not is_known_unit(unit):
            match = None
    if match is None:
        # Valor e unidade em células separadas: `NOME | 27.0 | U/L`.
        unit_text = last.rstrip(".").strip()
        if len(head) >= 2 and is_known_unit(unit_text) and _PLAIN_VALUE.match(head[-1]):
            value, unit, head = head[-1].strip(), unit_text, head[:-1]
        else:
            return None
    name = _join_name(head)
    return RowLine(name, value, unit) if name else None


def _from_single(text: str) -> RowLine | None:
    match = _SINGLE.match(text)
    if not match or not is_name(match["name"]):
        return None
    name = match["name"]
    if (
        _DECIMAL_TOKEN.search(name)
        or len(name.split()) > MAX_UNPIPED_NAME_WORDS
        or _PROSE_WORDS.search(fold(name))
    ):
        return None
    unit = match["unit"].strip().rstrip(".").strip()
    if not is_known_unit(unit, strict=True):
        return None
    return RowLine(match["name"].strip(), match["value"].strip(), unit)


def parse_row(line: str) -> RowLine | None:
    """`NOME | 40.7 ng/ml`, `NOME | 27.0 | U/L`, `NOME | NEGATIVO`, `NOME 40.7 ng/ml`...

    `None` quando a linha não é um resultado lido sem dúvida (inclusive células extras).
    """
    text = line.strip()
    cells = split_cells(text)
    return _from_cells(cells) if len(cells) >= 2 else _from_single(text)


def is_title_only(line: str) -> bool:
    """Linha só com um nome em CAIXA ALTA (`HEMOGRAMA`, `PSA TOTAL / PSA LIVRE`)."""
    text = line.strip()
    if "|" in text or ":" in text or not 3 <= len(text) <= 80:
        return False
    if sum(1 for ch in text if ch.isalpha()) < 3 or has_lowercase_word(text):
        return False
    return text[0].isalpha()


def is_meta(folded: str) -> bool:
    """Metadado, nota ou aviso (texto dobrado)."""
    return _META.match(folded) is not None


def is_footnote(line: str) -> bool:
    """`* Em adultos...`: nota de rodapé. Linha de tabela tem `|` ou outro `*` no meio."""
    text = line.strip()
    if not text.startswith("*") or " | " in text:
        return False
    return "*" not in text.lstrip("*")


def reference_header(line: str) -> str | None:
    """Texto que segue `VALORES DE REFERÊNCIA:` (pode ser vazio); `None` se não for o cabeçalho."""
    match = _REFERENCE_HEADER.match(fold(line).lstrip())
    if not match:
        return None
    offset = len(line) - len(line.lstrip())
    return line[offset + match.end() :].strip()


def is_percent_variant(name: str, title: str) -> bool:
    """`COLESTEROL HDL(%)` depois de `COLESTEROL HDL`: a mesma medida, em percentual."""
    return normalize_name(name) == f"{normalize_name(title)} %"


def is_closer(folded: str) -> bool:
    """`DATA DA COLETA:`/`DATA LIBERAÇÃO:`: o exame não traz mais resultados depois dela."""
    return _CLOSER.match(folded) is not None


def starts_method_or_material(folded: str) -> bool:
    return _METHOD_OR_MATERIAL.match(folded) is not None


def is_sex_columns(line: str) -> bool:
    """Cabeçalho `HOMENS | MULHERES` das colunas de referência do hemograma."""
    return _SEX_COLUMNS.match(fold(line).strip()) is not None


def parse_previous(line: str) -> list[tuple[date, str]] | None:
    """`Resultados Anteriores:24.0[02/08/2023]; 1.2[11/11/2022];` -> [(data, valor impresso)].

    `None` se a linha não for essa. Um par cortado na borda da página (`1.32[1`) é ignorado.
    """
    if not _PREVIOUS.match(fold(line).strip()):
        return None
    pairs: list[tuple[date, str]] = []
    for match in _PREVIOUS_PAIR.finditer(line):
        day, month, year = (int(part) for part in match["date"].split("/"))
        try:
            pairs.append((date(year, month, day), match["value"].replace(" ", "")))
        except ValueError:
            continue
    return pairs


def looks_like_result(cells: list[str], *, in_reference: bool) -> bool:
    """Linha em colunas com cara de resultado (`NOME | 4.83 | ...`) que `parse_row` não leu.

    Quem lê o bloco derruba o bloco inteiro nesse caso: um resultado ignorado em silêncio
    seria meia tabela. Dentro da seção de referência o teste é mais estreito (faixas por idade
    como `ALTO | 7 A 15 | 12 A 18` são texto de referência).
    """
    if len(cells) < 2 or not is_name(cells[0]):
        return False
    second = cells[1]
    if not _NUMBER_START.match(second) or _COMPARATOR.match(second):
        return False
    if not in_reference:
        return True
    return _RANGE_CELL.match(second) is None and re.match(r"^\d+\s*anos?", fold(second)) is None
