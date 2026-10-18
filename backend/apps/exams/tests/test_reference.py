from decimal import Decimal

import pytest

from apps.exams.extraction.flags import compute_flag, compute_qualitative_flag
from apps.exams.extraction.reference import (
    Band,
    applicable_bands,
    interpretation_for,
    normal_band,
    parse_reference,
)

D = Decimal


def select(text, sex="male", age=49):
    bands, needs_sex = applicable_bands(parse_reference(text), sex, age)
    return bands, needs_sex, normal_band(bands)


def test_simple_range_with_unit():
    band = parse_reference("4,50 a 6,50 milhões/uL")[0]
    assert (band.low, band.high) == (D("4.50"), D("6.50"))
    assert band.low_inclusive and band.high_inclusive


def test_thousands_separator_range():
    band = parse_reference("150.000 a 450.000 /uL")[0]
    assert (band.low, band.high) == (D("150000"), D("450000"))


def test_glucose_bands_and_interpretation():
    text = (
        "Normal: Glicemia de jejum entre 70 a 99 mg/dL\n"
        "Glicemia de jejum alterada: Glicemia de jejum entre 100 a 125 mg/dL\n"
        "Nota: Segundo os critérios diagnósticos da Associação Americana de Diabetes"
    )
    bands, _, normal = select(text)
    assert (normal.low, normal.high) == (D("70"), D("99"))
    assert interpretation_for(bands, D("103")) == "Glicemia de jejum alterada"
    assert interpretation_for(bands, D("90")) == "Normal"
    assert compute_flag(D("103"), "eq", normal) == "high"
    assert compute_flag(D("90"), "eq", normal) == "normal"


def test_a1c_upper_bound_is_exclusive():
    text = (
        "Normal: menor que 5,7\n"
        "Risco aumentado para diabetes : entre 5,7 e 6,4%\n"
        "Compatível com diabetes* : maior ou igual a 6,5%"
    )
    bands, _, normal = select(text)
    assert normal.high == D("5.7") and not normal.high_inclusive
    assert compute_flag(D("5.2"), "eq", normal) == "normal"
    assert compute_flag(D("5.7"), "eq", normal) == "high"
    assert interpretation_for(bands, D("6.0")) == "Risco aumentado para diabetes"


def test_lipid_desirable_with_age_prefix():
    text = "Acima de 20 anos: Com ou sem jejum de 12 horas:\nDesejável: Menor que 190 mg/dL"
    bands, _, normal = select(text)
    assert normal.high == D("190")
    assert compute_flag(D("192"), "eq", normal) == "high"


def test_hdl_lower_bound_exclusive():
    _, _, normal = select("Desejável: Maior que 40 mg/dL")
    assert normal.low == D("40") and not normal.low_inclusive
    assert compute_flag(D("44"), "eq", normal) == "normal"
    assert compute_flag(D("40"), "eq", normal) == "low"


def test_tsh_age_bands_pick_adult_and_skip_pregnancy():
    text = (
        "2 anos a menor que 12 anos: 0,640 a 6,270 uIU/mL\n"
        "12 anos à menor que 18 anos: 0,510 a 4,940 uIU/mL\n"
        "Igual ou maior que 18 anos : 0,550 a 4,780 uIU/mL\n"
        "GESTANTES: Primeiro trimestre 0,10 a 4,00 pUI/mL"
    )
    _, _, normal = select(text, age=49)
    assert (normal.low, normal.high) == (D("0.550"), D("4.780"))
    _, _, child = select(text, age=5)
    assert (child.low, child.high) == (D("0.640"), D("6.270"))


def test_fsh_men_adult_selected_children_and_women_skipped():
    text = (
        "Homens.\n"
        "Crianças Pré-Púberes: Até 1,2 mIU/mL\n"
        "Adultos.....: Até 15,0 mIU/mL\n"
        "Mulheres...:\n"
        "Crianças Pré-Púberes: Até 2,2 mIU/mL\n"
        "Fase folicular.....: 2,5 a 10,2 mIU/mL\n"
        "Pós menopausa: 23 a 116,3 mIU/mL"
    )
    _, _, normal = select(text, sex="male")
    assert normal.high == D("15.0") and normal.high_inclusive
    assert compute_flag(D("18.7"), "eq", normal) == "high"


def test_sex_required_when_only_sex_specific_bands():
    text = "Homens: 0,70 a 1,30 mg/dL\nMulheres: 0,50 a 1,10 mg/dL"
    bands, needs_sex, normal = select(text, sex=None)
    assert bands == [] and needs_sex and normal is None
    _, _, female = select(text, sex="female")
    assert (female.low, female.high) == (D("0.50"), D("1.10"))


def test_testosterone_normal_men_band_wins_over_hypogonadal():
    text = (
        "Crianças: 2,0 a 17,0 ng/dL\n"
        "Mulheres: 17,0 a 72,0 ng/dL\n"
        "Homens possivelmente hipogonádicos: 72 a 320 ng/dL\n"
        "Homens normais: 320 a 1440,9 ng/dL"
    )
    bands, _, normal = select(text)
    assert (normal.low, normal.high) == (D("320"), D("1440.9"))
    assert interpretation_for(bands, D("609.74")) == "Homens normais"
    assert interpretation_for(bands, D("200")) == "Homens possivelmente hipogonádicos"


def test_free_testosterone_age_and_sex_bands():
    text = (
        "HOMEM DE 21 A 49 ANOS : DE 3,03 A 14,80 ng/dL\n"
        "HOMEM DE 50 A 89 ANOS : DE 1,81 A 10,20 ng/dL\n"
        "MULHER DE 21 A 60 ANOS (PRÉ-MENOPAUSA): DE 0,08 A 1,11 ng/dL"
    )
    _, _, young = select(text, age=49)
    assert (young.low, young.high) == (D("3.03"), D("14.80"))
    _, _, older = select(text, age=50)
    assert (older.low, older.high) == (D("1.81"), D("10.20"))


def test_psa_age_band_does_not_apply_below_70():
    text = "Inferior a 4 ng/mL\nAcima de 70 anos até 4,4 ng/mL"
    _, _, normal = select(text, age=49)
    assert normal.high == D("4") and not normal.high_inclusive
    _, _, older = select(text, age=72)
    # A primeira faixa sem idade continua válida; a específica vem depois dela.
    assert [b.high for b in applicable_bands(parse_reference(text), "male", 72)[0]] == [
        D("4"),
        D("4.4"),
    ]
    assert older.high == D("4")


def test_risk_group_and_support_bands_are_never_auto_selected():
    text = (
        "População saudável (até 60 anos)...: acima de 20 ng/mL\n"
        "Grupos de risco....: 30 à 60 ng/mL\n"
        "Risco de toxicidade e hipercalcemia: acima de 100 ng/mL"
    )
    _, _, normal = select(text, age=49)
    assert normal.low == D("20") and not normal.low_inclusive
    assert compute_flag(D("42.3"), "eq", normal) == "normal"


def test_qualitative_reference():
    bands = parse_reference("Negativo")
    assert bands[0].qualitative == "negativo"
    assert compute_qualitative_flag("Positivo", bands) == "abnormal"
    assert compute_qualitative_flag("Negativo", bands) == "normal"


def test_unparseable_reference_returns_no_bands():
    assert parse_reference("Valores de referência de acordo com o Consenso") == []
    assert parse_reference("") == []


@pytest.mark.parametrize(
    ("comparator", "expected"),
    [("lt", "unknown"), ("eq", "high"), ("gt", "high")],
)
def test_comparator_cannot_assert_opposite_direction(comparator, expected):
    band = parse_reference("0 a 5")[0]
    assert compute_flag(D("9"), comparator, band) == expected


MERGED_TSH = (
    "2 anos a menor que 12 anos: 0,640 a 6,270 uIU/mL "
    "Igual ou maior que 18 anos : 0,550 a 4,780 uIU/mL"
)


def test_merged_tsh_line_yields_adult_and_child_bands():
    bands = parse_reference(MERGED_TSH)
    assert len(bands) == 2
    _, _, adult = select(MERGED_TSH, sex="male", age=49)
    assert (adult.low, adult.high) == (D("0.550"), D("4.780"))
    _, _, child = select(MERGED_TSH, sex="male", age=5)
    assert (child.low, child.high) == (D("0.640"), D("6.270"))
    _, _, teen = select(MERGED_TSH, sex="male", age=15)
    assert teen is None


def test_merged_sex_line_selects_by_sex():
    text = "Homens: 0,70 a 1,30 mg/dL. Mulheres: 0,50 a 1,10 mg/dL."
    _, _, male = select(text, sex="male")
    assert (male.low, male.high) == (D("0.70"), D("1.30"))
    _, _, female = select(text, sex="female")
    assert (female.low, female.high) == (D("0.50"), D("1.10"))
    bands, needs_sex, normal = select(text, sex=None)
    assert bands == [] and needs_sex and normal is None


def test_merged_glucose_line_keeps_both_categories():
    text = (
        "Normal: Glicemia de jejum entre 70 a 99 mg/dL "
        "Glicemia de jejum alterada: Glicemia de jejum entre 100 a 125 mg/dL"
    )
    bands, _, normal = select(text)
    assert (normal.low, normal.high) == (D("70"), D("99"))
    assert "alterada" in interpretation_for(bands, D("103"))
    assert interpretation_for(bands, D("90")) == "Normal"


def test_line_with_three_bands_picks_normal_adult_male():
    text = (
        "Crianças: 2,0 a 17,0 ng/dL Mulheres: 17,0 a 72,0 ng/dL Homens normais: 320 a 1440,9 ng/dL"
    )
    assert len(parse_reference(text)) == 3
    _, _, normal = select(text, sex="male", age=49)
    assert (normal.low, normal.high) == (D("320"), D("1440.9"))


def test_merged_line_round_trip_json():
    for band in parse_reference(MERGED_TSH):
        assert Band.from_json(band.to_json()) == band


def test_age_written_with_the_abbreviation_a_before_a_colon():
    # "20 - 59 a:" = 20 a 59 anos (a = anos): sem isto o "20 - 59" viraria uma faixa de resultado.
    bands = parse_reference("20 - 59 a: 0,45 a 4,5 mUI/L")
    assert len(bands) == 1
    assert (bands[0].low, bands[0].high) == (D("0.45"), D("4.5"))
    assert (bands[0].age_min, bands[0].age_max) == (20, 59)

    bands = parse_reference("Acima de 20 a: 0,9 a 1,7 ng/dL")
    assert len(bands) == 1
    assert (bands[0].low, bands[0].high, bands[0].age_min) == (D("0.9"), D("1.7"), 20)


def test_masc_and_fem_abbreviations_are_sexes_and_mais_de_is_a_minimum_age():
    text = "Masc: mais de 12 anos: 0,70 a 1,30 mg/dL Fem: mais de 12 anos: 0,50 a 1,10 mg/dL"
    bands = parse_reference(text)
    assert [(b.sex, b.age_min) for b in bands] == [("male", 12), ("female", 12)]

    bands = parse_reference("Maiores de 18 anos: 8,6 a 10,3 mg/dL")
    assert bands[0].age_min == 18
