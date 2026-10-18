import pytest
from django.contrib.auth import get_user_model

from apps.exams.catalog import sync_catalog
from apps.exams.extraction.analytes import AnalyteResolver, learn_alias
from apps.exams.extraction.fakes import FakeLLMClient
from apps.exams.extraction.text import normalize_name
from apps.exams.models import AliasSource, Analyte, AnalyteAlias, UnitConversion

pytestmark = pytest.mark.django_db


def _user(name):
    return get_user_model().objects.create_user(
        username=name, email=f"{name}@example.com", password="senha-forte-123"
    )


@pytest.fixture
def user_a():
    return _user("a")


@pytest.fixture
def user_b():
    return _user("b")


def _glicose_rows():
    return AnalyteAlias.objects.filter(user__isnull=True, alias_normalized="glicose")


def test_user_alias_does_not_leak_to_other_users_or_global(user_a, user_b):
    glicose = Analyte.objects.get(slug="glicose")
    tsh = Analyte.objects.get(slug="tsh")

    learn_alias(user_a, "Glicose", tsh)

    assert AnalyteResolver(user=user_b).resolve("Glicose").analyte == glicose
    assert AnalyteResolver(user=user_a).resolve("Glicose").analyte == tsh
    assert AnalyteResolver(user=None).resolve("Glicose").analyte == glicose
    assert AnalyteResolver().resolve("Glicose").analyte == glicose

    assert _glicose_rows().count() == 1
    assert _glicose_rows().get().analyte == glicose
    own = AnalyteAlias.objects.get(user=user_a, alias_normalized="glicose")
    assert own.analyte == tsh
    assert own.source == AliasSource.USER


def test_learn_alias_updates_only_own_row(user_a):
    tsh = Analyte.objects.get(slug="tsh")
    hba1c = Analyte.objects.get(slug="hba1c")

    learn_alias(user_a, "Glicose", tsh)
    learn_alias(user_a, "Glicose", hba1c)

    assert AnalyteAlias.objects.filter(user=user_a, alias_normalized="glicose").count() == 1
    assert AnalyteResolver(user=user_a).resolve("Glicose").analyte == hba1c
    assert _glicose_rows().get().analyte.slug == "glicose"


def test_learn_alias_requires_user():
    with pytest.raises(ValueError):
        learn_alias(None, "Glicose", Analyte.objects.get(slug="tsh"))
    assert _glicose_rows().get().analyte.slug == "glicose"


def test_llm_match_is_never_persisted(user_a):
    llm = FakeLLMClient(matches={"Nome Estranho": "tsh"})
    resolver = AnalyteResolver(llm, user=user_a)

    first = resolver.resolve("Nome Estranho")
    second = resolver.resolve("Nome Estranho")

    assert first.analyte is not None and first.analyte.slug == "tsh"
    assert first.source == "llm"
    assert second.analyte == first.analyte
    assert not AnalyteAlias.objects.filter(
        alias_normalized=normalize_name("Nome Estranho")
    ).exists()

    # Outra instância não herda nada: o palpite vive só no resolver.
    assert AnalyteResolver(user=user_a).resolve("Nome Estranho").analyte is None


def test_fuzzy_respects_user_scope(user_a, user_b):
    tsh = Analyte.objects.get(slug="tsh")
    learn_alias(user_a, "Marcador Exotico Zeta", tsh)

    found = AnalyteResolver(user=user_a).resolve("Marcador Exotico Zet")
    assert found.analyte == tsh
    assert found.source == "fuzzy"

    for resolver in (AnalyteResolver(user=user_b), AnalyteResolver()):
        missed = resolver.resolve("Marcador Exotico Zet")
        assert missed.analyte is None
        assert missed.source == "none"


def test_sync_catalog_is_idempotent_and_keeps_user_alias(user_a):
    tsh = Analyte.objects.get(slug="tsh")
    learn_alias(user_a, "Glicose", tsh)
    before = AnalyteAlias.objects.count()

    sync_catalog(Analyte, AnalyteAlias, UnitConversion)
    sync_catalog(Analyte, AnalyteAlias, UnitConversion)

    assert AnalyteAlias.objects.count() == before
    assert _glicose_rows().count() == 1
    own = AnalyteAlias.objects.get(user=user_a, alias_normalized="glicose")
    assert own.analyte == tsh
    assert own.source == AliasSource.USER


def test_deleting_user_cascades_aliases(user_a, user_b):
    tsh = Analyte.objects.get(slug="tsh")
    learn_alias(user_a, "Glicose", tsh)
    learn_alias(user_b, "Glicose", tsh)

    user_a.delete()

    assert not AnalyteAlias.objects.filter(user__email="a@example.com").exists()
    assert AnalyteAlias.objects.filter(user=user_b).count() == 1
    assert _glicose_rows().count() == 1
