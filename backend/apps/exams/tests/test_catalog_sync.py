"""`sync_catalog`: em lote (poucas consultas) e com o mesmo comportamento de sempre."""

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from apps.exams.catalog import CATALOG, sync_catalog
from apps.exams.extraction.text import normalize_name
from apps.exams.models import Analyte, AnalyteAlias, UnitConversion

pytestmark = pytest.mark.django_db

MAX_QUERIES = 20  # a versão linha a linha passava de mil


def sync() -> int:
    return sync_catalog(Analyte, AnalyteAlias, UnitConversion)


def test_a_fresh_sync_and_a_repeat_use_a_handful_of_queries():
    Analyte.objects.all().delete()

    with CaptureQueriesContext(connection) as first:
        assert sync() == len(CATALOG)
    with CaptureQueriesContext(connection) as again:
        assert sync() == len(CATALOG)

    assert len(first) <= MAX_QUERIES, [q["sql"][:80] for q in first.captured_queries]
    assert len(again) <= MAX_QUERIES


def test_sync_is_idempotent_and_writes_nothing_on_the_second_run():
    sync()
    before = (Analyte.objects.count(), AnalyteAlias.objects.count(), UnitConversion.objects.count())

    with CaptureQueriesContext(connection) as ctx:
        sync()

    assert (
        Analyte.objects.count(),
        AnalyteAlias.objects.count(),
        UnitConversion.objects.count(),
    ) == before
    writes = [
        q["sql"] for q in ctx.captured_queries if q["sql"].lstrip().startswith(("INSERT", "UPDATE"))
    ]
    assert writes == []  # float do catálogo vs Decimal do banco não pode contar como "mudou"


def test_every_catalog_entry_lands_with_its_values():
    sync()

    for order, entry in enumerate(CATALOG):
        analyte = Analyte.objects.get(slug=entry.slug)
        assert (analyte.name_pt, analyte.canonical_unit, analyte.sort_order) == (
            entry.name,
            entry.unit,
            order,
        )
        assert (
            AnalyteAlias.objects.filter(
                analyte=analyte, user__isnull=True, alias_normalized=normalize_name(entry.name)
            ).exists()
            or AnalyteAlias.objects.filter(
                user__isnull=True, alias_normalized=normalize_name(entry.name)
            ).exists()
        )


def test_changed_values_are_restored_and_untouched_rows_are_not_rewritten():
    sync()
    entry = CATALOG[0]
    Analyte.objects.filter(slug=entry.slug).update(name_pt="Nome alterado", sort_order=999)

    with CaptureQueriesContext(connection) as ctx:
        sync()

    analyte = Analyte.objects.get(slug=entry.slug)
    assert analyte.name_pt == entry.name and analyte.sort_order == 0
    updates = [q["sql"] for q in ctx.captured_queries if q["sql"].lstrip().startswith("UPDATE")]
    assert len(updates) == 1  # um único UPDATE em lote, só com a linha alterada


def test_a_changed_conversion_factor_is_restored():
    sync()
    entry = next(e for e in CATALOG if e.conversions)
    from_unit, _ = entry.conversions[0]
    UnitConversion.objects.filter(analyte__slug=entry.slug, from_unit=from_unit).update(
        factor="123.00000000", to_unit="x"
    )

    sync()

    conv = UnitConversion.objects.get(analyte__slug=entry.slug, from_unit=from_unit)
    assert conv.to_unit == entry.unit and conv.factor != 123


def test_user_aliases_are_never_touched(django_user_model):
    sync()
    user = django_user_model.objects.create_user("a@example.com", "a@example.com", "x-12345-yz")
    analyte = Analyte.objects.get(slug=CATALOG[0].slug)
    mine = AnalyteAlias.objects.create(
        analyte=analyte, user=user, alias_normalized="meu apelido", source="user"
    )

    sync()

    mine.refresh_from_db()
    assert (mine.analyte_id, mine.user_id, mine.source) == (analyte.pk, user.pk, "user")
    assert AnalyteAlias.objects.filter(alias_normalized="meu apelido").count() == 1


def test_an_existing_global_alias_is_not_repointed_to_another_exam():
    sync()
    alias = AnalyteAlias.objects.filter(user__isnull=True).first()
    other = Analyte.objects.exclude(pk=alias.analyte_id).first()
    AnalyteAlias.objects.filter(pk=alias.pk).update(analyte=other)

    sync()

    alias.refresh_from_db()
    assert alias.analyte_id == other.pk
