"""Unidades: normalização de grafias e conversões entre escalas."""

import re
from decimal import Decimal

from .text import fold

# Chave: unidade dobrada, sem espaços, com µ->u e superescritos como dígitos.
_UNIT_MAP = {
    "mg/dl": "mg/dL",
    "g/dl": "g/dL",
    "g/l": "g/L",
    "mg/l": "mg/L",
    "ng/ml": "ng/mL",
    "pg/ml": "pg/mL",
    "ng/dl": "ng/dL",
    "ug/dl": "µg/dL",
    "ug/l": "µg/L",
    "ug/ml": "µg/mL",
    "mmol/l": "mmol/L",
    "umol/l": "µmol/L",
    "nmol/l": "nmol/L",
    "pmol/l": "pmol/L",
    "meq/l": "mEq/L",
    "u/l": "U/L",
    "ui/l": "UI/L",
    "ui/ml": "UI/mL",
    "uiu/ml": "µUI/mL",
    "uui/ml": "µUI/mL",
    "miu/ml": "mUI/mL",
    "mui/ml": "mUI/mL",
    "fl": "fL",
    "pg": "pg",
    "%": "%",
    "milhoes/ul": "milhões/µL",
    "milhoes/mm3": "milhões/µL",
    "mil/mm3": "mil/µL",
    "mil/ul": "mil/µL",
    "x103/ul": "mil/µL",
    "10^3/ul": "mil/µL",
    "/ul": "/µL",
    "/mm3": "/µL",
    "ml/min/1.73m2": "mL/min/1,73 m²",
    "ml/min/1,73m2": "mL/min/1,73 m²",
    "mm/h": "mm/h",
    # Grafias do laboratório da "ficha" para a mesma unidade.
    "microg/dl": "µg/dL",
    "mcg/dl": "µg/dL",
    "microg/l": "µg/L",
    "mcg/l": "µg/L",
    "microg/ml": "µg/mL",
    "mcg/ml": "µg/mL",
    "microgramas/ml": "µg/mL",
    "mu/l": "mUI/L",
    "mm/1ah": "mm/h",
    # Grafias do laboratório "Softl@b": a mesma unidade escrita de outro jeito (1 dL = 100 mL;
    # picog = pg; nanog = ng; µ3 = µm³ = fL; micro UI = µUI; IU = UI).
    "ng/100ml": "ng/dL",
    "ug/100ml": "µg/dL",
    "mg/100ml": "mg/dL",
    "picog/ml": "pg/mL",
    "nanog/ml": "ng/mL",
    "micromol/l": "µmol/L",
    "microui/ml": "µUI/mL",
    "iu/ml": "UI/mL",
    "iu/l": "UI/L",
    "u3": "fL",
    "um3": "fL",
    # Velocidade de hemossedimentação: o laudo imprime só "mm" (milímetros na 1ª hora).
    "mm": "mm/h",
}

_SUPERSCRIPTS = str.maketrans({"³": "3", "²": "2", "¹": "1", "μ": "u", "µ": "u"})

# Mesma grandeza, escala diferente: (de, para) -> fator multiplicativo.
GENERIC_FACTORS: dict[tuple[str, str], Decimal] = {
    ("mil/µL", "/µL"): Decimal("1000"),
    ("/µL", "mil/µL"): Decimal("0.001"),
    ("milhões/µL", "/µL"): Decimal("1000000"),
    ("/µL", "milhões/µL"): Decimal("0.000001"),
    ("UI/L", "mUI/mL"): Decimal("1"),
    ("mUI/mL", "UI/L"): Decimal("1"),
    ("g/L", "g/dL"): Decimal("0.1"),
    ("g/dL", "g/L"): Decimal("10"),
    ("mg/L", "mg/dL"): Decimal("0.1"),
    ("mg/dL", "mg/L"): Decimal("10"),
    # Mesma massa por volume, escrita de outro jeito (valem para qualquer exame em massa).
    ("µg/L", "ng/mL"): Decimal("1"),
    ("ng/mL", "µg/L"): Decimal("1"),
    ("ng/L", "pg/mL"): Decimal("1"),
    ("pg/mL", "ng/L"): Decimal("1"),
    ("ng/dL", "pg/mL"): Decimal("10"),
    ("pg/mL", "ng/dL"): Decimal("0.1"),
    ("mUI/L", "µUI/mL"): Decimal("1"),
    ("µUI/mL", "mUI/L"): Decimal("1"),
}

COUNT_UNITS = frozenset({"/µL", "mil/µL", "milhões/µL"})

KNOWN_UNITS = frozenset(_UNIT_MAP.values())
# Unidade "com cara de unidade" mesmo fora do mapa: sem espaço, começa com letra, µ, % ou /
# e traz uma barra (UI/L, mg/24h). Evita aceitar "a 999,9 UI/L" (faixa) como unidade.
_UNIT_SHAPE = re.compile(r"^[A-Za-zµμ/%][A-Za-zµμ0-9²³^.,%/\-]*$")


def _key(raw: str) -> str:
    return fold(raw.translate(_SUPERSCRIPTS)).replace(" ", "")


def normalize_unit(raw: str | None) -> str:
    """Grafia canônica da unidade, ou o texto limpo se desconhecida."""
    text = (raw or "").strip()
    if not text:
        return ""
    mapped = _UNIT_MAP.get(_key(text))
    return mapped if mapped else text


def is_known_unit(unit: str, *, strict: bool = False) -> bool:
    """A grafia é uma unidade conhecida (ou, sem `strict`, tem cara de unidade com barra)."""
    text = unit.strip()
    if not text:
        return False
    if text == "%" or normalize_unit(text) in KNOWN_UNITS:
        return True
    if strict:
        return False
    return "/" in text and _UNIT_SHAPE.match(text) is not None and not text[0].isdigit()


def is_count_unit(unit: str) -> bool:
    return unit in COUNT_UNITS


def convert(
    value: Decimal,
    from_unit: str,
    to_unit: str,
    analyte_factors: dict[tuple[str, str], Decimal] | None = None,
) -> Decimal | None:
    """Converte entre unidades; `None` quando não há fator conhecido."""
    if not from_unit or not to_unit or from_unit == to_unit:
        return value
    pair = (from_unit, to_unit)
    factor = (analyte_factors or {}).get(pair) or GENERIC_FACTORS.get(pair)
    if factor is None:
        return None
    return value * factor


def infer_unit(text: str | None) -> str:
    """Última unidade reconhecida num texto (ex.: a referência '4,5 a 6,5 milhões/uL')."""
    for token in reversed(re.findall(r"\S+", text or "")):
        mapped = _UNIT_MAP.get(_key(token.strip("(),;.")))
        if mapped:
            return mapped
    return ""
