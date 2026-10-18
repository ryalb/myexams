from decimal import Decimal

import pytest

from apps.exams.extraction.units import convert, normalize_unit


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("mg/dl", "mg/dL"),
        ("MG/DL", "mg/dL"),
        ("ng/dl", "ng/dL"),
        ("uIU/mL", "µUI/mL"),
        ("µIU/mL", "µUI/mL"),
        ("mIU/mL", "mUI/mL"),
        ("milhões/uL", "milhões/µL"),
        ("milhões/mm³", "milhões/µL"),
        ("/mm³", "/µL"),
        ("/uL", "/µL"),
        ("fl.", "fl."),
        ("fL", "fL"),
        ("ml/min/1,73 m²", "mL/min/1,73 m²"),
        ("", ""),
        ("xyz", "xyz"),
        ("microg/L", "µg/L"),
        ("mcg/dL", "µg/dL"),
        ("microgramas/mL", "µg/mL"),
        ("mU/L", "mUI/L"),
        # laboratório Softl@b: mesmas unidades, outras grafias
        ("ng/100ml", "ng/dL"),
        ("ug/100ml", "µg/dL"),
        ("mg/100ml", "mg/dL"),
        ("picog/ml", "pg/mL"),
        ("PICOG/ML", "pg/mL"),
        ("nanog/ml", "ng/mL"),
        ("micromol/l", "µmol/L"),
        ("micro UI/ml", "µUI/mL"),
        ("IU/ml", "UI/mL"),
        ("u3", "fL"),
        ("µm³", "fL"),
        ("mm", "mm/h"),
        ("/ mm3", "/µL"),
    ],
)
def test_normalize_unit(raw, expected):
    assert normalize_unit(raw) == expected


def test_convert_same_unit_is_identity():
    assert convert(Decimal("5"), "mg/dL", "mg/dL") == Decimal("5")


def test_convert_generic_scale():
    assert convert(Decimal("7.5"), "mil/µL", "/µL") == Decimal("7500.0")


def test_convert_analyte_specific():
    factors = {("mmol/L", "mg/dL"): Decimal("18.016")}
    assert convert(Decimal("5"), "mmol/L", "mg/dL", factors) == Decimal("90.080")


def test_convert_unknown_pair_is_none():
    assert convert(Decimal("5"), "mmol/L", "mg/dL") is None


def test_convert_same_mass_concentration_written_differently():
    assert convert(Decimal("125"), "µg/L", "ng/mL") == Decimal("125")
    assert convert(Decimal("911"), "ng/L", "pg/mL") == Decimal("911")
    assert convert(Decimal("4.9"), "ng/dL", "pg/mL") == Decimal("49.0")


def test_convert_milli_units_per_liter_to_micro_units_per_milliliter():
    assert convert(Decimal("19"), "mUI/L", "µUI/mL") == Decimal("19")


def test_known_unit_accepts_the_mapped_spellings_and_slash_shaped_units_only():
    from apps.exams.extraction.units import is_known_unit

    assert is_known_unit("ng/100ml")
    assert is_known_unit("%")
    assert is_known_unit("mg/24h")  # tem cara de unidade (barra)
    assert not is_known_unit("mg/24h", strict=True)
    assert not is_known_unit("A 999,9 UI/L")
    assert not is_known_unit("min 15 seg")
    assert not is_known_unit("")
