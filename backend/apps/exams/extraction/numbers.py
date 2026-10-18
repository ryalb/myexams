"""Números e comparadores como impressos nos laudos (pt-BR por padrão).

O LLM só transcreve o texto do valor; quem interpreta é este módulo. No
`finances` o modelo "corrigia" o formato brasileiro para o americano por conta
própria, então aqui o formato nunca é decidido pelo modelo.
"""

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from .text import fold

NUMBER_PATTERN = r"\d+(?:[.,]\d+)*"
_NUMBER = re.compile(NUMBER_PATTERN)

# (regex sobre texto dobrado, comparador). Mais específicos primeiro.
_COMPARATOR_PREFIXES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(p), c)
    for p, c in [
        (r"^(?:<=|=<|≤|menor ou igual a|ate)\s*", "lte"),
        (r"^(?:>=|=>|≥|maior ou igual a|igual ou maior que|igual ou superior a)\s*", "gte"),
        (r"^(?:<|menor que|inferior a|abaixo de)\s*", "lt"),
        (r"^(?:>|maior que|superior a|acima de)\s*", "gt"),
    ]
]


@dataclass(frozen=True)
class ParsedNumber:
    value: Decimal | None
    comparator: str = "eq"
    # Ponto seguido de exatamente 3 dígitos ("1.251"): milhar ou decimal?
    ambiguous: bool = False
    # Texto do valor quando não há número ("Negativo", "Não reagente").
    text: str = ""


def parse_digits(
    digits: str,
    plausible: tuple[Decimal, Decimal] | None = None,
    *,
    integer_only: bool = False,
) -> tuple[Decimal, bool]:
    """Interpreta uma string só de dígitos e separadores.

    Retorna (valor, ambíguo). Regras:
    - vírgula e ponto juntos: o último separador é o decimal;
    - só vírgula: decimal (pt-BR), a menos que haja várias (milhar);
    - só ponto: várias -> milhar; uma com 3 dígitos depois -> ambíguo, resolvido
      pela faixa plausível do exame quando só uma leitura cabe nela;
    - `integer_only` (contagens por µL): o ponto único é sempre milhar.
    """
    s = digits.strip().rstrip(".,")
    has_comma, has_dot = "," in s, "." in s
    try:
        if has_comma and has_dot:
            last = max(s.rfind(","), s.rfind("."))
            decimal_sep = s[last]
            thousands_sep = "," if decimal_sep == "." else "."
            return Decimal(s.replace(thousands_sep, "").replace(decimal_sep, ".")), False
        if has_comma:
            if s.count(",") > 1:
                return Decimal(s.replace(",", "")), False
            return Decimal(s.replace(",", ".")), False
        if has_dot:
            if s.count(".") > 1:
                return Decimal(s.replace(".", "")), False
            head, tail = s.split(".")
            if len(tail) == 3 and 1 <= len(head) <= 3 and head != "0":
                as_thousands, as_decimal = Decimal(head + tail), Decimal(s)
                if integer_only:
                    return as_thousands, False
                if plausible is not None:
                    low, high = plausible
                    fits_thousands = low <= as_thousands <= high
                    fits_decimal = low <= as_decimal <= high
                    if fits_thousands and not fits_decimal:
                        return as_thousands, False
                    if fits_decimal and not fits_thousands:
                        return as_decimal, False
                return as_thousands, True
            return Decimal(s), False
        return Decimal(s), False
    except InvalidOperation as exc:
        raise ValueError(f"número inválido: {digits!r}") from exc


def parse_number(
    raw: str | None,
    plausible: tuple[Decimal, Decimal] | None = None,
    *,
    integer_only: bool = False,
) -> ParsedNumber:
    """Valor impresso -> número + comparador. Sem número, devolve o texto.

    `integer_only`: o exame é uma contagem inteira (ex.: células/µL), então um
    ponto seguido de 3 dígitos é sempre separador de milhar.
    """
    text = (raw or "").strip()
    if not text:
        return ParsedNumber(value=None)

    folded = fold(text)
    comparator = "eq"
    for pattern, code in _COMPARATOR_PREFIXES:
        match = pattern.match(folded)
        if match:
            comparator = code
            text = text[match.end() :].strip()
            folded = folded[match.end() :]
            break

    match = _NUMBER.search(folded)
    if not match:
        return ParsedNumber(value=None, text=(raw or "").strip())

    negative = match.start() > 0 and folded[match.start() - 1] == "-"
    try:
        value, ambiguous = parse_digits(match.group(0), plausible, integer_only=integer_only)
    except ValueError:
        return ParsedNumber(value=None, text=(raw or "").strip())
    if negative:
        value = -value
    return ParsedNumber(value=value, comparator=comparator, ambiguous=ambiguous)
