"""Sinalização do resultado em relação à faixa de referência do próprio laudo."""

from decimal import Decimal

from ..models import Comparator, ResultFlag
from .reference import Band
from .text import fold

_PRINTED_HIGH = {"h", "high", "alto", "alta", "↑", "elevado", "acima"}
_PRINTED_LOW = {"l", "low", "baixo", "baixa", "↓", "abaixo"}
_NEGATIVE_WORDS = ("negativo", "nao reagente", "ausente", "ausencia")
_POSITIVE_WORDS = ("positivo", "reagente", "presente")


def compute_flag(value: Decimal | None, comparator: str, band: Band | None) -> str:
    if value is None or band is None or band.qualitative:
        return ResultFlag.UNKNOWN
    if band.low is None and band.high is None:
        return ResultFlag.UNKNOWN

    flag = ResultFlag.NORMAL
    if band.low is not None and (
        value < band.low or (value == band.low and not band.low_inclusive)
    ):
        flag = ResultFlag.LOW
    elif band.high is not None and (
        value > band.high or (value == band.high and not band.high_inclusive)
    ):
        flag = ResultFlag.HIGH

    # "<0,5" não permite afirmar que está acima; ">200" não permite afirmar abaixo.
    if comparator in (Comparator.LT, Comparator.LTE) and flag == ResultFlag.HIGH:
        return ResultFlag.UNKNOWN
    if comparator in (Comparator.GT, Comparator.GTE) and flag == ResultFlag.LOW:
        return ResultFlag.UNKNOWN
    return flag


def compute_qualitative_flag(value_text: str, bands: list[Band]) -> str:
    """Compara 'Positivo'/'Reagente'... com uma referência qualitativa."""
    reference = next((b for b in bands if b.qualitative), None)
    if reference is None or not value_text:
        return ResultFlag.UNKNOWN
    value = fold(value_text)
    expected_negative = any(w in fold(reference.qualitative) for w in _NEGATIVE_WORDS)
    is_negative = any(w in value for w in _NEGATIVE_WORDS)
    is_positive = not is_negative and any(w in value for w in _POSITIVE_WORDS)
    if not (is_negative or is_positive):
        return ResultFlag.UNKNOWN
    if expected_negative:
        return ResultFlag.NORMAL if is_negative else ResultFlag.ABNORMAL
    return ResultFlag.ABNORMAL if is_negative else ResultFlag.NORMAL


def printed_flag_direction(printed: str) -> str | None:
    """'H'/'L'/'ALTO'... impresso pelo laboratório -> 'high'/'low'/None."""
    token = fold(printed).strip(" *.()")
    if token in _PRINTED_HIGH:
        return ResultFlag.HIGH
    if token in _PRINTED_LOW:
        return ResultFlag.LOW
    return None
