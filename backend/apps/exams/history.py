"""Séries históricas por exame e visão geral (gráfico + tabela)."""

from datetime import datetime, time

from django.utils import timezone

from .extraction.units import convert
from .models import Analyte, AnalyteKind, ExamResult, Profile, ResultFlag


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


def _age_years(birth_date, today) -> int | None:
    if birth_date is None:
        return None
    had_birthday = (today.month, today.day) >= (birth_date.month, birth_date.day)
    years = today.year - birth_date.year - (0 if had_birthday else 1)
    return years if years >= 0 else None


def build_report(user) -> dict:
    """Todos os exames com o histórico completo, em uma consulta, para o relatório em Markdown.

    Privacidade: o relatório sai do app (vai a um modelo remoto), então o perfil entra só como
    sexo e idade em anos (nunca nome, e-mail nem data de nascimento).
    """
    results = (
        ExamResult.objects.filter(user=user, analyte__isnull=False)
        .select_related("analyte", "document", "document__laboratory")
        .prefetch_related("analyte__conversions")
        .order_by("analyte__category", "analyte__sort_order", "analyte__pk", "collected_at", "pk")
    )
    grouped: dict[int, dict] = {}
    for result in results:
        analyte = result.analyte
        entry = grouped.get(analyte.pk)
        if entry is None:
            factors = {(c.from_unit, c.to_unit): c.factor for c in analyte.conversions.all()}
            entry = grouped[analyte.pk] = {
                "analyte": analyte,
                "factors": factors,
                "points": [],
            }
        entry["points"].append(serialize_point(result, analyte, entry["factors"]))

    analytes = []
    for entry in grouped.values():
        analyte = entry["analyte"]
        points = sorted(
            entry["points"], key=lambda p: (p["collected_at"] is None, p["collected_at"] or "")
        )
        analytes.append(
            {
                "slug": analyte.slug,
                "name": analyte.name_pt,
                "category": analyte.category,
                "unit": analyte.canonical_unit,
                "kind": analyte.kind,
                "points": points,
            }
        )

    profile = Profile.objects.filter(user=user).first()
    return {
        "generated_at": timezone.now().isoformat(),
        "profile": {
            "sex": profile.sex if profile else "",
            "age": _age_years(profile.birth_date, timezone.localdate()) if profile else None,
        },
        "analytes": analytes,
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
