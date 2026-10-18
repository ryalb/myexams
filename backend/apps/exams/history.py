"""Séries históricas por exame e visão geral (gráfico + tabela)."""

from datetime import datetime, time

from django.utils import timezone

from .extraction.units import convert
from .models import Analyte, AnalyteKind, ExamResult, ResultFlag


def _when(result: ExamResult) -> datetime | None:
    if result.collected_at:
        return result.collected_at
    on = result.document.collected_on
    return timezone.make_aware(datetime.combine(on, time.min)) if on else None


def _num(value) -> float | None:
    return None if value is None else float(value)


def _ref_in_canonical(result: ExamResult, analyte: Analyte, factors, value):
    if value is None:
        return None
    converted = convert(value, result.unit, analyte.canonical_unit, factors)
    return _num(converted)


def serialize_point(result: ExamResult, analyte: Analyte, factors) -> dict:
    when = _when(result)
    return {
        "id": result.pk,
        "document_id": result.document_id,
        "laboratory": result.document.laboratory.name if result.document.laboratory else "",
        "origin": result.origin,
        "collected_at": when.isoformat() if when else None,
        "value": _num(result.value_canonical),
        "value_raw": result.value_raw,
        "value_text": result.value_text,
        "comparator": result.comparator,
        "unit": result.unit,
        "ref_low": _ref_in_canonical(result, analyte, factors, result.ref_low),
        "ref_high": _ref_in_canonical(result, analyte, factors, result.ref_high),
        "reference_raw": result.reference_raw,
        "flag": result.flag,
        "interpretation": result.interpretation,
        "review_status": result.review_status,
    }


def build_analyte_history(user, analyte: Analyte) -> dict:
    factors = {(c.from_unit, c.to_unit): c.factor for c in analyte.conversions.all()}
    results = (
        ExamResult.objects.filter(user=user, analyte=analyte)
        .select_related("document", "document__laboratory")
        .order_by("collected_at", "pk")
    )
    points = [serialize_point(r, analyte, factors) for r in results]
    points.sort(key=lambda p: (p["collected_at"] is None, p["collected_at"] or ""))
    chartable = analyte.kind == AnalyteKind.NUMERIC and any(p["value"] is not None for p in points)
    return {
        "analyte": {
            "id": analyte.pk,
            "slug": analyte.slug,
            "name": analyte.name_pt,
            "category": analyte.category,
            "unit": analyte.canonical_unit,
            "kind": analyte.kind,
        },
        "chartable": chartable,
        "points": points,
    }


def build_overview(user) -> list[dict]:
    """Último valor, variação desde o anterior e sparkline por exame."""
    results = (
        ExamResult.objects.filter(user=user, analyte__isnull=False)
        .select_related("analyte", "document", "document__laboratory")
        .order_by("analyte__category", "analyte__sort_order", "collected_at", "pk")
    )
    grouped: dict[int, list[ExamResult]] = {}
    for result in results:
        grouped.setdefault(result.analyte_id, []).append(result)

    overview = []
    for rows in grouped.values():
        rows.sort(key=lambda r: (_when(r) is None, _when(r) or timezone.now()))
        analyte = rows[0].analyte
        numeric = [r for r in rows if r.value_canonical is not None]
        latest = rows[-1]
        previous = numeric[-2] if len(numeric) >= 2 else None
        delta = delta_pct = None
        if previous is not None and latest.value_canonical is not None:
            delta = float(latest.value_canonical - previous.value_canonical)
            if previous.value_canonical:
                delta_pct = delta / float(previous.value_canonical) * 100
        when = _when(latest)
        overview.append(
            {
                "slug": analyte.slug,
                "name": analyte.name_pt,
                "category": analyte.category,
                "unit": analyte.canonical_unit,
                "kind": analyte.kind,
                "count": len(rows),
                "latest_value": _num(latest.value_canonical),
                "latest_text": latest.value_raw,
                "latest_at": when.isoformat() if when else None,
                "latest_flag": latest.flag or ResultFlag.UNKNOWN,
                "latest_interpretation": latest.interpretation,
                "delta": delta,
                "delta_pct": delta_pct,
                "sparkline": [float(r.value_canonical) for r in numeric[-12:]],
            }
        )
    return overview
