"""Resultado digitado pelo usuário (origem `manual`).

Nada é inventado aqui: número, comparador, unidade canônica, faixa de referência, sinalização e
avisos saem das MESMAS funções puras da importação e do PATCH (`recompute_result`,
`rederived_warnings`). Só a entrada vem do usuário em vez do PDF.
"""

from datetime import date, datetime

from django.utils import timezone

from ..codes import WarningCode
from ..models import Analyte, ExamDocument, ExamResult, ResultOrigin, ReviewStatus
from .analytes import AnalyteResolver
from .recompute import recompute_result, rederived_warnings
from .text import normalize_name
from .units import normalize_unit
from .validate import needs_review, warning


def build_manual_result(
    *,
    user,
    document: ExamDocument,
    name_raw: str,
    value_raw: str,
    unit_raw: str,
    reference_raw: str,
    collected_at: datetime | None,
    notes: str,
    analyte: Analyte | None,
    resolver: AnalyteResolver,
    sex: str | None,
    birth_date: date | None,
    page: int,
    position: int,
) -> ExamResult:
    """Monta (sem gravar) o resultado digitado, já com os campos derivados e os avisos.

    Sem `analyte`, tenta o catálogo pelo nome (apelidos); sem correspondência o exame fica nulo
    e o aviso `unmapped_analyte` pede revisão, como na extração.
    """
    if analyte is None:
        analyte = resolver.resolve(name_raw, normalize_unit(unit_raw)).analyte
    result = ExamResult(
        user=user,
        document=document,
        analyte=analyte,
        origin=ResultOrigin.MANUAL,
        page=page,
        position=position,
        name_raw=name_raw,
        value_raw=value_raw,
        unit_raw=unit_raw,
        reference_raw=reference_raw,
        collected_at=collected_at,
        notes=notes,
        raw={"source": "manual"},
    )
    parsed = recompute_result(result, sex=sex, birth_date=birth_date, resolver=resolver)
    warnings = rederived_warnings(result, [])
    if parsed.ambiguous:
        warnings.append(warning(WarningCode.AMBIGUOUS_NUMBER_FORMAT))
    result.warnings = warnings
    # Digitado pelo usuário = decisão humana (`user_corrected`, como no PATCH), a menos que
    # os avisos peçam uma segunda olhada (exame fora do catálogo, unidade, valor implausível...).
    result.review_status = (
        ReviewStatus.NEEDS_REVIEW if needs_review(warnings) else ReviewStatus.USER_CORRECTED
    )
    return result


def duplicate_key(name_raw: str, collected_at: datetime | None, value_raw: str) -> tuple:
    """Chave do guarda de duplicidade: nome normalizado + dia da coleta + valor impresso."""
    day = timezone.localtime(collected_at).date() if collected_at else None
    return (normalize_name(name_raw), day, " ".join(value_raw.split()).casefold())
