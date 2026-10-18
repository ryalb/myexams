"""Valores de referência impressos -> faixas estruturadas.

O texto de referência dos laudos é livre e traz faixas por sexo, idade, condição
e categoria ("Normal: 70 a 99 / Glicemia de jejum alterada: 100 a 125"). Cada
linha vira uma `Band`; `applicable_bands` escolhe as aplicáveis ao paciente.
Tudo determinístico: o LLM só entrega o texto, nunca interpreta a faixa.
"""

import re
from dataclasses import asdict, dataclass
from decimal import Decimal

from .numbers import NUMBER_PATTERN, parse_digits
from .text import fold

N = NUMBER_PATTERN

_SEX_WORDS = re.compile(r"\b(homens?|masculino|masc|mulheres?|feminino|fem)\b")
_MALE = {"homem", "homens", "masculino", "masc"}

# "a" colado a ":" é a abreviação de "anos" ("20 - 59 a: 0,45 a 4,5 mUI/L"); "a" no meio é "até".
_YEARS = r"(?:anos?|a(?=\s*:))"
_AGE_RANGE = re.compile(
    rf"(?:de\s+)?({N})\s*(?:anos?)?\s*(?:a|ate|-|–|—)\s*(menor que\s*)?({N})\s*{_YEARS}"
)
_AGE_MIN = re.compile(
    rf"(?:igual ou maior que|igual ou superior a|maior ou igual a|acima de|maior que"
    rf"|superior a|a partir de|mais de|maiores de)\s*({N})\s*{_YEARS}"
)
_AGE_MAX = re.compile(rf"(ate|abaixo de|menor que|inferior a)\s*({N})\s*anos?")

# "0 a 1 mês", "1 a 12 anos": faixa de idade, não de resultado.
_AGE_WORD_AHEAD = r"(?!\s*(?:anos?|mes(?:es)?|dias?|semanas?)\b)"
_TWO_SIDED = re.compile(
    rf"(?:entre\s+({N})\s*e\s*({N}))"
    rf"|(?:(?:de\s+)?(?<![\d/:])({N})\s*(?:a|-|–|—)\s*({N}){_AGE_WORD_AHEAD})"
)
# Idade em meses/dias/semanas (lactentes): a faixa que vem depois é só deles.
_INFANT = re.compile(r"\b\d+\s*(?:mes(?:es)?|dias?|semanas?)\b")
_UPPER = re.compile(rf"(<=|=<|≤|menor ou igual a|<|menor que|inferior a|abaixo de|ate)\s*({N})")
_LOWER = re.compile(
    rf"(>=|=>|≥|maior ou igual a|igual ou maior que|igual ou superior a|a partir de"
    rf"|>|maior que|superior a|acima de)\s*({N})"
)
_UPPER_INCLUSIVE = {"<=", "=<", "≤", "menor ou igual a", "ate"}
_LOWER_INCLUSIVE = {
    ">=",
    "=>",
    "≥",
    "maior ou igual a",
    "igual ou maior que",
    "igual ou superior a",
    "a partir de",
}

_QUALITATIVE = re.compile(
    r"\b(nao reagente|negativo|ausente|ausencia|positivo|reagente|presente)\b"
)

# Rótulos que descrevem uma categoria de interpretação (não a faixa "normal").
_INTERPRETATION_WORDS = (
    "alterad",
    "risco",
    "compativel",
    "limitrof",
    "alto",
    "elevad",
    "deficien",
    "insuficien",
    "toxicidade",
    "hipogonad",
    "baixo",
    "diabet",
    "pre-diabet",
    "intermediari",
)
_NORMAL_WORDS = ("normal", "normai", "desejavel", "otimo", "adequad", "saudavel", "sem risco")
# Contextos que não valem para um adulto comum: nunca escolhidos automaticamente.
_CONDITION_WORDS = (
    "crianca",
    "pre-pubere",
    "pre pubere",
    "gestant",
    "menopausa",
    "lactante",
    "fase ",
    "pico ",
    "ovulat",
    "grupo",
    "trimestre",
    "sensibilidade",
    "neonat",
    "recem",
)
# Unidade logo após a faixa ("uIU/mL", "mg/dL", "%", "/uL", "mL/min/1,73 m²", "mg"): é pulada
# para não contaminar o rótulo da faixa seguinte.
_UNIT = re.compile(
    r"\s*(?:%"
    r"|(?:[a-zµμ][a-zµμ0-9³²^.]*)?(?:/[a-zµμ0-9³²^,.]+)+(?:\s*m[²2])?"
    r"|(?:mg|g|ui|fl|pg|u|meq|mmol|umol|nmol|miu|mui|uiu|pui|mmhg)(?![a-zµμ0-9]))"
)
_LABEL_NOISE = re.compile(r"^(?:entre|de|em|para|valores?|referencia)\b\s*|\s*(?:entre|de)$")


@dataclass
class Band:
    label: str
    low: Decimal | None
    high: Decimal | None
    low_inclusive: bool = True
    high_inclusive: bool = True
    sex: str | None = None
    age_min: int | None = None
    age_max: int | None = None
    age_max_inclusive: bool = True
    condition: str = ""
    normal_like: bool = True
    qualitative: str = ""
    text: str = ""

    def contains(self, value: Decimal) -> bool:
        if self.low is not None and (
            value < self.low or (value == self.low and not self.low_inclusive)
        ):
            return False
        if self.high is not None and (
            value > self.high or (value == self.high and not self.high_inclusive)
        ):
            return False
        return self.low is not None or self.high is not None

    def to_json(self) -> dict:
        data = asdict(self)
        data["low"] = None if self.low is None else str(self.low)
        data["high"] = None if self.high is None else str(self.high)
        return data

    @classmethod
    def from_json(cls, data: dict) -> "Band":
        data = dict(data)
        data["low"] = None if data.get("low") is None else Decimal(data["low"])
        data["high"] = None if data.get("high") is None else Decimal(data["high"])
        return cls(**data)


def _dec(text: str) -> Decimal:
    return parse_digits(text)[0]


def _blank(text: str, start: int, end: int) -> str:
    return text[:start] + " " * (end - start) + text[end:]


def _find_age(folded: str, pos: int):
    """Primeira expressão de idade a partir de `pos`: (start, end, min, max, max_inclusive).

    `min`/`max` são None quando a expressão não define aquele limite.
    """
    candidates = []
    match = _AGE_RANGE.search(folded, pos)
    if match:
        low, high = int(_dec(match.group(1))), int(_dec(match.group(3)))
        candidates.append((match.start(), 0, match.end(), low, high, not match.group(2)))
    match = _AGE_MIN.search(folded, pos)
    if match:
        candidates.append((match.start(), 1, match.end(), int(_dec(match.group(1))), None, True))
    match = _AGE_MAX.search(folded, pos)
    if match:
        high = int(_dec(match.group(2)))
        candidates.append((match.start(), 2, match.end(), None, high, match.group(1) == "ate"))
    if not candidates:
        return None
    start, _, end, age_min, age_max, max_inclusive = min(candidates, key=lambda c: (c[0], c[1]))
    return start, end, age_min, age_max, max_inclusive


def _find_range(folded: str, pos: int = 0):
    """Primeira expressão de faixa a partir de `pos`: (start, end, low, high, low_inc, high_inc)."""
    candidates = []
    match = _TWO_SIDED.search(folded, pos)
    if match:
        low, high = (
            (match.group(1), match.group(2)) if match.group(1) else (match.group(3), match.group(4))
        )
        candidates.append((match.start(), 0, match.end(), _dec(low), _dec(high), True, True))
    match = _UPPER.search(folded, pos)
    if match:
        inclusive = match.group(1) in _UPPER_INCLUSIVE
        candidates.append(
            (match.start(), 1, match.end(), None, _dec(match.group(2)), True, inclusive)
        )
    match = _LOWER.search(folded, pos)
    if match:
        inclusive = match.group(1) in _LOWER_INCLUSIVE
        candidates.append(
            (match.start(), 1, match.end(), _dec(match.group(2)), None, inclusive, True)
        )
    if not candidates:
        return None
    start, _, end, low, high, low_inc, high_inc = min(candidates, key=lambda c: (c[0], c[1]))
    return start, end, low, high, low_inc, high_inc


def _pick_label(prefix: str) -> str:
    segments = [seg.strip(" .:-–—*()\t") for seg in prefix.split(":")]
    segments = [_LABEL_NOISE.sub("", seg).strip(" .:-–—*()") for seg in segments if seg.strip()]
    segments = [seg for seg in segments if seg]
    if not segments:
        return ""
    keywords = _INTERPRETATION_WORDS + _NORMAL_WORDS
    for seg in segments:
        if any(word in fold(seg) for word in keywords):
            return seg
    return segments[-1]


def _sex_of(folded: str) -> str | None:
    """Sexo da última palavra de sexo do trecho (a mais próxima da faixa)."""
    matches = list(_SEX_WORDS.finditer(folded))
    if not matches:
        return None
    return "male" if matches[-1].group(1) in _MALE else "female"


def _parse_line(line: str, context_sex: str | None) -> tuple[list[Band], str | None]:
    """Varre a linha da esquerda para a direita; devolve (faixas, novo context_sex).

    Uma linha pode trazer várias faixas ("0,64 a 6,27 uIU/mL Igual ou maior que 18 anos:
    0,55 a 4,78 uIU/mL"). Idade vira contexto pendente da próxima faixa; o texto entre o fim
    da faixa anterior (e sua unidade) e o início da próxima é o rótulo.
    """
    original = line
    folded = fold(original)
    bands: list[Band] = []
    own_sex: list[bool] = []
    pos = seg_start = 0
    age_min = age_max = None
    max_inclusive = True

    while True:
        age = _find_age(folded, pos)
        found = _find_range(folded, pos)
        if age is None and found is None:
            break
        if age is not None and (found is None or age[0] <= found[0]):
            start, end, new_min, new_max, new_inclusive = age
            if new_min is not None:
                age_min = new_min
            if new_max is not None:
                age_max, max_inclusive = new_max, new_inclusive
            folded = _blank(folded, start, end)
            original = _blank(original, start, end)
            pos = end
            continue

        start, end, low, high, low_inc, high_inc = found
        prefix = original[seg_start:start]
        label_folded = folded[seg_start:start]
        prefix_sex = _sex_of(label_folded)
        if prefix_sex:
            context_sex = prefix_sex
        label = _pick_label(prefix)
        condition = next((w.strip() for w in _CONDITION_WORDS if w in label_folded + " "), "")
        condition = condition or ("lactente" if _INFANT.search(label_folded) else "")
        interpretation = any(w in fold(label) for w in _INTERPRETATION_WORDS)
        normal_word = any(w in fold(label) for w in _NORMAL_WORDS)
        bands.append(
            Band(
                label=label,
                low=low,
                high=high,
                low_inclusive=low_inc,
                high_inclusive=high_inc,
                sex=context_sex,
                age_min=age_min,
                age_max=age_max,
                age_max_inclusive=max_inclusive,
                condition=label if condition else "",
                normal_like=normal_word or not interpretation,
                text=line,
            )
        )
        own_sex.append(prefix_sex is not None)
        age_min = age_max = None
        max_inclusive = True
        unit = _UNIT.match(folded, end)
        pos = seg_start = unit.end() if unit else end

    rest = folded[seg_start:]
    rest_sex = _sex_of(rest)
    if not bands:
        # Sem faixa numérica: a linha só atualiza o sexo e/ou vira faixa qualitativa.
        if rest_sex:
            context_sex = rest_sex
        qualitative = _QUALITATIVE.search(folded)
        if qualitative:
            bands.append(
                Band(
                    label=_pick_label(original[: qualitative.start()]),
                    low=None,
                    high=None,
                    sex=context_sex,
                    age_min=age_min,
                    age_max=age_max,
                    age_max_inclusive=max_inclusive,
                    qualitative=qualitative.group(1),
                    text=line,
                )
            )
        return bands, context_sex

    if len(bands) == 1:
        # Um único resultado na linha: sexo/idade escritos depois dele ainda o qualificam.
        band = bands[0]
        if rest_sex and not own_sex[0]:
            band.sex = context_sex = rest_sex
        if band.age_min is None and band.age_max is None:
            band.age_min, band.age_max, band.age_max_inclusive = age_min, age_max, max_inclusive
    elif rest_sex:
        context_sex = rest_sex
    return bands, context_sex


def parse_reference(text: str | None) -> list[Band]:
    """Texto de referência -> lista de faixas (vazia se nada for reconhecido)."""
    bands: list[Band] = []
    context_sex: str | None = None
    for line in (text or "").splitlines():
        line = line.strip()
        if line:
            line_bands, context_sex = _parse_line(line, context_sex)
            bands.extend(line_bands)
    return bands


def applicable_bands(
    bands: list[Band], sex: str | None, age: int | None
) -> tuple[list[Band], bool]:
    """Faixas aplicáveis ao paciente. O bool diz se faltou o sexo para decidir."""
    selected: list[Band] = []
    needs_sex = False
    for band in bands:
        if band.condition:
            continue
        if band.sex and not sex:
            needs_sex = True
            continue
        if band.sex and band.sex != sex:
            continue
        if band.age_min is not None or band.age_max is not None:
            if age is None:
                continue
            if band.age_min is not None and age < band.age_min:
                continue
            if band.age_max is not None:
                above = age > band.age_max or (age == band.age_max and not band.age_max_inclusive)
                if above:
                    continue
        selected.append(band)
    return selected, needs_sex


def normal_band(bands: list[Band]) -> Band | None:
    return next((b for b in bands if b.normal_like and not b.qualitative), None)


def interpretation_for(bands: list[Band], value: Decimal) -> str:
    """Rótulo da categoria que contém o valor (ex.: 'Glicemia de jejum alterada')."""
    for band in bands:
        if band.qualitative or not band.label or not band.contains(value):
            continue
        if not band.normal_like or any(w in fold(band.label) for w in _NORMAL_WORDS):
            return band.label
    return ""
