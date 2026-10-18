from decimal import Decimal

import pytest

from apps.exams.extraction.numbers import parse_number

D = Decimal


@pytest.mark.parametrize(
    ("raw", "value", "comparator"),
    [
        ("13,5", D("13.5"), "eq"),
        ("103", D("103"), "eq"),
        ("1.234,5", D("1234.5"), "eq"),
        ("1,234.5", D("1234.5"), "eq"),
        ("1.234.567", D("1234567"), "eq"),
        ("0,5", D("0.5"), "eq"),
        ("0.500", D("0.5"), "eq"),
        ("1.5", D("1.5"), "eq"),
        ("<0,5", D("0.5"), "lt"),
        ("< 0,5", D("0.5"), "lt"),
        ("≥ 6,5", D("6.5"), "gte"),
        ("Superior a 90", D("90"), "gt"),
        ("inferior a 4", D("4"), "lt"),
        ("Até 15,0", D("15.0"), "lte"),
        ("5,2 %", D("5.2"), "eq"),
    ],
)
def test_parse_number(raw, value, comparator):
    parsed = parse_number(raw)
    assert parsed.value == value
    assert parsed.comparator == comparator
    assert not parsed.ambiguous


@pytest.mark.parametrize("raw", ["Negativo", "Não reagente", "", None, "Ausente"])
def test_parse_number_without_number_keeps_text(raw):
    parsed = parse_number(raw)
    assert parsed.value is None
    assert parsed.text == (raw or "").strip()


def test_dot_with_three_digits_is_ambiguous_without_hint():
    parsed = parse_number("4.000")
    assert parsed.value == D("4000")
    assert parsed.ambiguous


def test_ambiguous_dot_resolved_by_plausible_range():
    # Leucócitos: 4.0 /µL seria implausível, então é milhar.
    assert parse_number("4.000", (D("500"), D("100000"))).value == D("4000")
    # TSH: 1251 µUI/mL seria implausível, então é decimal.
    tsh = parse_number("1.251", (D("0.005"), D("200")))
    assert tsh.value == D("1.251")
    assert not tsh.ambiguous


def test_ambiguous_when_both_readings_are_plausible():
    parsed = parse_number("1.251", (D("0"), D("100000")))
    assert parsed.ambiguous


@pytest.mark.parametrize(
    ("raw", "value"),
    [
        ("2.400", D("2400")),
        ("1.251", D("1251")),
        ("12.345", D("12345")),
        ("850", D("850")),
        ("1.234.567", D("1234567")),
    ],
)
def test_integer_only_treats_dot_as_thousands_separator(raw, value):
    # Contagem por µL é inteira: o ponto nunca é decimal, mesmo com faixa larga.
    parsed = parse_number(raw, (D("0"), D("100000")), integer_only=True)
    assert parsed.value == value
    assert not parsed.ambiguous
    assert parse_number(raw, integer_only=True).value == value
    assert not parse_number(raw, integer_only=True).ambiguous


def test_integer_only_false_keeps_default_behavior():
    assert parse_number("4.000").ambiguous
    assert parse_number("4.000", integer_only=False).ambiguous
    assert parse_number("1.251", (D("0"), D("100000")), integer_only=False).ambiguous
