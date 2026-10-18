"""Exames que o laboratório Softl@b imprime e o catálogo precisava ter.

Sem entrada própria, a busca por similaridade trocava o nome por outro parecido (Vitamina A virava
Vitamina D, Mielócitos virava Monócitos, Apolipoproteína B virava A-1). Os nomes abaixo têm de
casar por apelido exato.
"""

import pytest

from apps.exams.extraction.analytes import AnalyteResolver
from apps.exams.models import Analyte

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize(
    ("printed", "unit", "slug"),
    [
        ("VITAMINA A", "mg/L", "vitamina_a"),
        ("VITAMINA E", "mg/L", "vitamina_e"),
        ("VITAMINA B12", "pg/mL", "vitamina_b12"),
        ("25 - HIDROXIVITAMINA D", "ng/mL", "vitamina_d"),
        ("MIELÓCITOS", "%", "mielocitos_pct"),
        ("MIELÓCITOS (absoluto)", "/µL", "mielocitos_abs"),
        ("METAMIELÓCITOS", "%", "metamielocitos_pct"),
        ("PROMIELÓCITOS (absoluto)", "/µL", "promielocitos_abs"),
        ("BLASTOS", "%", "blastos_pct"),
        ("LINFÓCITOS TÍPICOS", "%", "linfocitos_pct"),
        ("LINFÓCITOS TÍPICOS (absoluto)", "/µL", "linfocitos_abs"),
        ("LINFÓCITOS ATÍPICOS", "%", "linfocitos_atipicos_pct"),
        ("LINFÓCITOS ATÍPICOS (absoluto)", "/µL", "linfocitos_atipicos_abs"),
        ("APOLIPROTEÍNA A", "mg/dL", "apolipoproteina_a1"),
        ("APOLIPOPROTEINA B", "mg/dL", "apolipoproteina_b"),
        ("COLESTEROL HDL(%)", "%", "colesterol_hdl_pct"),
        ("COLESTEROL HDL", "mg/dL", "colesterol_hdl"),
        ("% HEMOGLOBINA A1C", "%", "hba1c"),
        ("LH - ESPECÍFICO", "mUI/mL", "lh"),
        ("T3", "ng/dL", "t3_total"),
        ("T3 REVERSO", "ng/mL", "t3_reverso"),
        ("ESTRONA", "pg/mL", "estrona"),
        ("PROGESTERONA", "ng/mL", "progesterona"),
        ("PSA LIVRE", "ng/mL", "psa_livre"),
        ("CÁLCIO SÉRICO", "mg/dL", "calcio"),
        ("ERITROSSEDIMENTAÇÃO NA 1ª HORA", "mm/h", "vhs"),
        ("H.C.M.", "pg", "hcm"),
        ("PROTEÍNAS TOTAIS", "g/dL", "proteinas_totais"),
        ("GLICOSE PÓS PRANDIAL", "mg/dL", "glicose_pos_prandial"),
        ("CREATINOFOSFOQUINASE - CPK", "U/L", "cpk"),
        ("DEHIDROGENASE LACTICA", "U/L", "ldh"),
        ("SULFATO DE DHEA", "µg/dL", "dhea_s"),
        ("PARATORMÔNIO (MOLÉCULA INTEIRA)", "pg/mL", "pth"),
        ("ANTI - PEROXIDASE OU ANTI - TPO", "UI/mL", "anti_tpo"),
        ("ANTICORPO ANTI-TIREOGLOBULINA", "UI/mL", "anti_tireoglobulina"),
        ("IGF-1 SOMATOMEDINA C", "ng/mL", "igf1"),
        ("IGFBP - 3 PROTEINA LIGADORA-3 DO IGF", "µg/mL", "igfbp3"),
        ("HOMOCISTEÍNA", "µmol/L", "homocisteina"),
        ("ZINCO", "µg/dL", "zinco"),
        ("SELÊNIO SÉRICO", "µg/L", "selenio"),
    ],
)
def test_printed_name_resolves_by_exact_alias_to_its_own_exam(printed, unit, slug):
    resolution = AnalyteResolver().resolve(printed, unit)

    assert resolution.source == "alias"
    assert resolution.analyte is not None
    assert resolution.analyte.slug == slug


def test_new_exams_have_a_canonical_unit_and_plausibility_limits():
    slugs = [
        "vitamina_a",
        "vitamina_e",
        "mielocitos_pct",
        "apolipoproteina_b",
        "colesterol_hdl_pct",
        "psa_livre",
        "cpk",
    ]

    for analyte in Analyte.objects.filter(slug__in=slugs):
        assert analyte.canonical_unit
        assert analyte.plausible_min is not None and analyte.plausible_max is not None
    assert Analyte.objects.filter(slug__in=slugs).count() == len(slugs)


def test_psa_ratio_stays_out_of_the_catalog_because_labs_print_it_as_fraction_or_percent():
    assert not Analyte.objects.filter(slug="psa_relacao").exists()
