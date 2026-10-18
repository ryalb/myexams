"""Campos derivados em código (o modelo só transcreve seis colunas)."""

from apps.exams.extraction.blocks import Block
from apps.exams.extraction.pipeline import _coerce_row, find_section, find_source_line

HEMOGRAM = [
    "HEMOGRAMA",
    "Material: sangue",
    "Método: Automatizado",
    "Data da coleta: 25/08/2026 - 07:01:57",
    "ERITROGRAMA | Valores de referência",
    "Hemácias | 4,85 | milhões/uL | 4,50 a 6,50 milhões/uL",
    "Hemoglobina | g/dL | 13,50 a 18,00 g/dL",
    "LEUCOGRAMA",
    "Leucócitos | 4.000 | /uL | 4.000 a 10.000 /uL",
    "Neutrófilos | 60,0 % | 2.400 /uL | 40,0 a 75,0 | 2.000 a 7.000",
]


def block(lines: list[str], title: str = "") -> Block:
    return Block(title=title, lines=lines, page=1)


# --- find_source_line ---------------------------------------------------------------------


def test_source_line_contains_the_value_ignoring_spaces():
    b = block(HEMOGRAM, "HEMOGRAMA")
    assert find_source_line(b, "Leucócitos", "4.000") == (
        "Leucócitos | 4.000 | /uL | 4.000 a 10.000 /uL"
    )
    assert find_source_line(b, "Neutrófilos (absoluto)", "2.400").startswith("Neutrófilos |")


def test_source_line_prefers_the_line_that_also_has_the_name():
    lines = ["Colesterol total | 190 mg/dL", "HDL | 190 mg/dL", "Triglicerídeos | 95 mg/dL"]
    assert find_source_line(block(lines), "HDL", "190") == "HDL | 190 mg/dL"
    assert (
        find_source_line(block(lines), "Colesterol total", "190") == "Colesterol total | 190 mg/dL"
    )


def test_source_line_falls_back_to_first_line_with_the_value():
    lines = ["Qualquer coisa | 42 mg/dL", "Outra | 42 mg/dL"]
    assert find_source_line(block(lines), "Nome que não existe", "42") == lines[0]


def test_source_line_is_empty_when_value_is_not_in_the_block():
    assert find_source_line(block(HEMOGRAM), "Magnésio", "2,9") == ""


def test_source_line_without_value_uses_the_line_with_the_name():
    assert find_source_line(block(HEMOGRAM), "Hemoglobina", "") == (
        "Hemoglobina | g/dL | 13,50 a 18,00 g/dL"
    )
    assert find_source_line(block(HEMOGRAM), "Inexistente", "") == ""


def test_source_line_accent_and_case_insensitive_name():
    lines = ["Linha | 7", "NEUTRÓFILOS | 7"]
    assert find_source_line(block(lines), "Neutrofilos", "7") == "NEUTRÓFILOS | 7"


# --- find_section -------------------------------------------------------------------------


def test_section_is_the_nearest_preceding_all_caps_title():
    b = block(HEMOGRAM, "HEMOGRAMA")
    assert find_section(b, HEMOGRAM[5]) == "ERITROGRAMA"  # título com cabeçalho de colunas
    assert find_section(b, HEMOGRAM[6]) == "ERITROGRAMA"
    assert find_section(b, HEMOGRAM[8]) == "LEUCOGRAMA"
    assert find_section(b, HEMOGRAM[9]) == "LEUCOGRAMA"


def test_section_is_empty_without_a_title_before_the_row():
    lines = ["GLICOSE | 103 mg/dL", "Material: soro", "Valor de Referência: 70 a 99 mg/dL"]
    assert find_section(block(lines, "GLICOSE | 103 mg/dL"), lines[0]) == ""


def test_exam_title_and_meta_lines_are_not_sections():
    lines = [
        "CREATININA",
        "Material: SORO",
        "MÉTODO: ENZIMÁTICO",
        "DATA DA COLETA",
        "VALOR DE REFERÊNCIA",
        "CREATININA: | 1,20 mg/dL",
    ]
    assert find_section(block(lines, "CREATININA"), lines[-1]) == ""


def test_title_with_digits_or_mixed_case_or_too_long_is_not_a_section():
    for title in ("VITAMINA B12", "Eritrograma", "A" * 41, "LEUCOGRAMA - 2"):
        lines = [title, "Hemácias | 4,85 | milhões/uL"]
        assert find_section(block(lines), lines[1]) == "", title
    assert find_section(block(["A" * 40, "Hemácias | 4,85"]), "Hemácias | 4,85") == "A" * 40


def test_result_row_in_columns_is_not_taken_as_a_section():
    lines = ["ASPECTO | LÍMPIDO", "COR | AMARELA", "Densidade | 1,020"]
    assert find_section(block(lines), lines[2]) == ""


def test_section_requires_the_source_line_to_be_in_the_block():
    assert find_section(block(HEMOGRAM), "") == ""
    assert find_section(block(HEMOGRAM), "linha que não existe") == ""


# --- _coerce_row --------------------------------------------------------------------------


def test_coerce_row_tolerates_missing_keys_and_ignores_extras():
    row = _coerce_row({"name": " Glicose ", "value_text": 103, "section": "X", "method": "Y"})
    assert row == {
        "name": "Glicose",
        "value_text": "103",
        "unit": "",
        "reference_text": "",
        "printed_flag": "",
        "is_calculated": False,
    }


def test_coerce_row_keeps_is_calculated_only_for_true():
    assert _coerce_row({"is_calculated": True})["is_calculated"] is True
    assert _coerce_row({"is_calculated": "false"})["is_calculated"] is False
