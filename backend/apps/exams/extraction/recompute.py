"""Reavalia um resultado editado pelo usuário (sem LLM)."""

from datetime import date

from django.utils import timezone

from ..models import ExamResult, ResultFlag
from .analytes import AnalyteResolver
from .dates import age_on
from .flags import compute_flag, compute_qualitative_flag
from .numbers import parse_number
from .pipeline import _is_integer_count_unit, band_kind
from .reference import applicable_bands, interpretation_for, normal_band, parse_reference
from .units import convert, normalize_unit


def recompute_result(
    result: ExamResult, *, sex: str | None, birth_date: date | None, resolver: AnalyteResolver
) -> None:
    analyte = result.analyte
    result.unit = normalize_unit(result.unit_raw) or result.unit
    plausible = None
    if analyte and analyte.plausible_min is not None and analyte.plausible_max is not None:
        if not result.unit or result.unit == analyte.canonical_unit:
            plausible = (analyte.plausible_min, analyte.plausible_max)

    # Contagem por µL é inteira: `5.600` é cinco mil e seiscentos (igual ao pipeline).
    parsed = parse_number(
        result.value_raw, plausible, integer_only=_is_integer_count_unit(result.unit)
    )
    result.value_numeric = parsed.value
    result.comparator = parsed.comparator
    result.value_text = "" if parsed.value is not None else (parsed.text or result.value_raw)

    result.value_canonical = None
    if parsed.value is not None:
        if analyte and analyte.canonical_unit and result.unit:
            result.value_canonical = convert(
                parsed.value, result.unit, analyte.canonical_unit, resolver.factors(analyte)
            )
        else:
            result.value_canonical = parsed.value

    when = (
        timezone.localtime(result.collected_at).date()
        if result.collected_at
        else timezone.localdate()
    )
    bands = parse_reference(result.reference_raw)
    selected, _ = applicable_bands(bands, sex, age_on(birth_date, when))
    normal = normal_band(selected)
    result.ref_bands = [b.to_json() for b in bands]
    qualitative_band = next((b for b in selected if b.qualitative), None)
    result.ref_kind = band_kind(normal or qualitative_band)
    result.ref_low = normal.low if normal else None
    result.ref_high = normal.high if normal else None
    result.ref_low_inclusive = normal.low_inclusive if normal else True
    result.ref_high_inclusive = normal.high_inclusive if normal else True

    if parsed.value is not None:
        result.flag = compute_flag(parsed.value, parsed.comparator, normal)
        result.interpretation = interpretation_for(selected, parsed.value)[:255]
    else:
        result.flag = compute_qualitative_flag(result.value_text, selected) or ResultFlag.UNKNOWN
        result.interpretation = ""
