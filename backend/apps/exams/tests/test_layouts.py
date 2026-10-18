"""Camada determinística (`layouts`): blocos de um exame só lidos sem LLM.

Blocos sintéticos que imitam as formas impressas pelos laboratórios.
"""

import pytest

from apps.exams.extraction.blocks import Block
from apps.exams.extraction.layouts import (
    extract_inline_block,
    looks_like_table,
    reference_for,
)


def block(lines: list[str]) -> Block:
    return Block(title=lines[0], lines=lines, page=1)


GLUCOSE = [
    "GLICOSE | 103 mg/dL",
    "Material: soro",
    "Método: Hexoquinase",
    "Data da coleta: 25/08/2026 - 07:01:51",
    "Valor de Referência: | Normal: Glicemia de jejum entre 70 a 99 mg/dL",
    "Glicemia de jejum alterada: Glicemia de jejum entre 100 a 125 mg/dL",
    "Nota: Segundo os critérios diagnósticos da Associação Americana de",
    "Diabetes e endossado pela Sociedade Brasileira de Diabetes.",
    "Anteriores: | 25/04/2022 - 92 | 17/02/2018 - 84",
]

CREATININE = [
    "CREATININA",
    "Material: soro",
    "Método: Jaffe, picrato alcalino, cinético",
    "Data da coleta: | 25/08/2026 - 07:01:51",
    "CREATININA: | 1,20 mg/dL",
    "Valores de Referência: Homens: 0,70 a 1,30 mg/dL",
    "Anteriores: 25/04/2022 - 0,97 | 18/07/2017 - 1,02",
    "eRFG: | 74 mL/min/1,73 m²",
    "Método: CÁLCULO PELA FÓRMULA CKD-EPI (2021)",
    "Valoresde Referência: eRFG normal: > 90 mL/min/1,73 m²",
    "* A estimativa de filtração glomerular não é calculada em menores",
    "de dezoito anos.",
    "Atenção para mudança do intervalo de referência a partir de 01/01/2025.",
]

HEMOGRAM = [
    "HEMOGRAMA",
    "Material: sangue",
    "Método: Automatizado",
    "ERITROGRAMA | Valores de referência",
    "Hemácias | 4,85 milhões/uL | 4,50 a 6,50 milhões/uL",
    "Hemoglobina | 14,1 g/dL | 13,50 a 18,00 g/dL",
    "Hematócrito | 42,0 % | 40,0 a 54,0 %",
    "Observação: | Hemácias normocíticas",
]


# --- exame com resultado no título -------------------------------------------------------


def test_title_inline_block_becomes_one_row_with_reference_by_code():
    rows = extract_inline_block(block(GLUCOSE))
    assert rows == [
        {
            "name": "GLICOSE",
            "value_text": "103",
            "unit": "mg/dL",
            "reference_text": (
                "Normal: Glicemia de jejum entre 70 a 99 mg/dL\n"
                "Glicemia de jejum alterada: Glicemia de jejum entre 100 a 125 mg/dL"
            ),
            "printed_flag": "",
            "is_calculated": False,
            "source_line": "GLICOSE | 103 mg/dL",
        }
    ]


def test_note_and_previous_results_are_not_part_of_the_reference():
    (row,) = extract_inline_block(block(GLUCOSE))
    assert "Nota" not in row["reference_text"]
    assert "Diabetes" not in row["reference_text"]
    assert "Anteriores" not in row["reference_text"]
    assert "92" not in row["reference_text"]


def test_title_without_bar_is_accepted_when_the_unit_is_known():
    lines = ["GLICOSE 103 mg/dL", "Material: soro", "Valor de Referência: 70 a 99 mg/dL"]
    (row,) = extract_inline_block(block(lines))
    assert (row["name"], row["value_text"], row["unit"]) == ("GLICOSE", "103", "mg/dL")
    assert row["reference_text"] == "70 a 99 mg/dL"


@pytest.mark.parametrize(
    ("line", "value", "unit"),
    [
        ("TSH ULTRA SENSÍVEL | 1,942 uIU/mL", "1,942", "uIU/mL"),
        ("SÓDIO | 140,0 mmol/L", "140,0", "mmol/L"),
        ("HEMATÓCRITO | 42,0 %", "42,0", "%"),
        ("PLAQUETAS | 250.000 /uL", "250.000", "/uL"),
        ("PSA | < 0,5 ng/mL", "< 0,5", "ng/mL"),
        ("PCR | >= 10 mg/L", ">= 10", "mg/L"),
        ("FERRITINA | ≤ 15 ng/mL", "≤ 15", "ng/mL"),
        ("TGO (AST) | 25 U/L", "25", "U/L"),
        ("25-HIDROXI VITAMINA D | 31,2 ng/mL", "31,2", "ng/mL"),
        ("FLUXO | 12 mg/24h", "12", "mg/24h"),  # unidade fora do mapa, mas com cara de unidade
    ],
)
def test_title_values_units_and_comparators(line, value, unit):
    lines = [line, "Material: soro", "Valor de Referência: | 1 a 2 mg/dL"]
    (row,) = extract_inline_block(block(lines))
    assert (row["value_text"], row["unit"]) == (value, unit)
    assert row["name"] == line.split(" | ")[0]


def test_block_without_a_reference_gets_an_empty_reference():
    lines = ["SÓDIO | 140 mmol/L", "Material: soro", "Método: Eletrodo"]
    (row,) = extract_inline_block(block(lines))
    assert row["reference_text"] == ""


def test_reference_with_continuation_lines_stops_before_prose_and_support_lab_note():
    lines = [
        "MAGNÉSIO | 2,1 mg/dL",
        "Material: soro",
        "Valor de Referência: | DE 1,6 A 2,6 mg/dL",
        "Exame realizado em laboratório de apoio LABORATÓRIO X.",
    ]
    (row,) = extract_inline_block(block(lines))
    assert row["reference_text"] == "DE 1,6 A 2,6 mg/dL"


def test_reference_stops_at_the_consensus_sentence_and_keeps_column_markers_out():
    lines = [
        "TESTOSTERONA LIVRE | 11,52 ng/dL",
        "Material: soro",
        "Valor de Referência: | HOMEM DE 21 A 49 ANOS | : DE 3,03 A 14,80 ng/dL",
        "HOMEM DE 50 A 89 ANOS | : DE 1,81 A 10,20 ng/dL",
        "Valores de referência de acordo com o Consenso Brasileiro.",
        "Anteriores: | 01/01/2020 - 10,0",
    ]
    (row,) = extract_inline_block(block(lines))
    assert row["reference_text"] == (
        "HOMEM DE 21 A 49 ANOS : DE 3,03 A 14,80 ng/dL\n"
        "HOMEM DE 50 A 89 ANOS : DE 1,81 A 10,20 ng/dL"
    )


def test_reference_stops_at_a_long_line_without_numbers():
    lines = [
        "VITAMINA D | 31 ng/mL",
        "Material: soro",
        "Valor de Referência: | acima de 30 ng/mL",
        "Grupos de risco: 20 a 30 ng/mL",
        "São considerados grupos de risco: idosos, gestantes, lactantes e pacientes",
        "com história de quedas e fraturas 99.",
    ]
    (row,) = extract_inline_block(block(lines))
    assert row["reference_text"] == "acima de 30 ng/mL\nGrupos de risco: 20 a 30 ng/mL"


def test_reference_is_capped_at_twelve_lines():
    lines = ["FSH | 5,0 mIU/mL", "Material: soro", "Valor de Referência: | Homens...:"]
    lines += [f"Fase {i}: {i} a {i + 1} mIU/mL" for i in range(1, 20)]
    (row,) = extract_inline_block(block(lines))
    assert len(row["reference_text"].splitlines()) == 12


def test_footnote_markers_end_the_reference():
    lines = [
        "HEMOGLOBINA GLICADA | 5,7 %",
        "Material: sangue",
        "Valor de referência: Normal: menor que 5,7%",
        "Compatível com diabetes* : maior ou igual a 6,5%",
        "*Quando confirmada em uma segunda ocasião.",
        "(1) Outra observação.",
    ]
    (row,) = extract_inline_block(block(lines))
    assert (
        row["reference_text"]
        == "Normal: menor que 5,7%\nCompatível com diabetes* : maior ou igual a 6,5%"
    )


# --- vários exames no bloco ---------------------------------------------------------------


def test_creatinine_and_calculated_erfg_each_get_the_reference_that_follows_them():
    rows = extract_inline_block(block(CREATININE))
    assert [r["name"] for r in rows] == ["CREATININA", "eRFG"]
    creatinine, erfg = rows
    assert (creatinine["value_text"], creatinine["unit"]) == ("1,20", "mg/dL")
    assert creatinine["reference_text"] == "Homens: 0,70 a 1,30 mg/dL"
    assert (erfg["value_text"], erfg["unit"]) == ("74", "mL/min/1,73 m²")
    assert erfg["reference_text"] == "eRFG normal: > 90 mL/min/1,73 m²"
    assert creatinine["source_line"] == "CREATININA: | 1,20 mg/dL"
    assert erfg["source_line"] == "eRFG: | 74 mL/min/1,73 m²"


def test_calculated_flag_comes_from_the_following_method_line():
    creatinine, erfg = extract_inline_block(block(CREATININE))
    assert creatinine["is_calculated"] is False  # o Método do eRFG vem depois do próximo valor
    assert erfg["is_calculated"] is True


@pytest.mark.parametrize("word", ["Cálculo", "calculado", "Fórmula", "formula"])
def test_calculated_by_method_words(word):
    lines = ["VLDL | 20 mg/dL", "Material: soro", f"Método: {word} de Friedewald"]
    (row,) = extract_inline_block(block(lines))
    assert row["is_calculated"] is True


@pytest.mark.parametrize(
    "title", ["TESTOSTERONA LIVRE CALCULADA | 11,5 ng/dL", "Glicose média estimada | 117 mg/dL"]
)
def test_calculated_by_the_name(title):
    (row,) = extract_inline_block(block([title, "Material: soro"]))
    assert row["is_calculated"] is True


def test_a_method_line_before_the_value_does_not_make_it_calculated():
    lines = ["GLICOSE | 90 mg/dL", "Material: soro", "Método: Hexoquinase"]
    (row,) = extract_inline_block(block(lines))
    assert row["is_calculated"] is False


def test_titled_exam_followed_by_a_labelled_estimated_value():
    lines = [
        "HEMOGLOBINA GLICADA | 5,7 %",
        "Material: sangue",
        "Valor de referência: Normal: menor que 5,7%",
        "*Quando confirmada em uma segunda ocasião.",
        "Glicose média estimada (GME): | 117,00 mg/dL",
        "Notas:",
    ]
    hba1c, gme = extract_inline_block(block(lines))
    assert hba1c["name"] == "HEMOGLOBINA GLICADA"
    assert hba1c["reference_text"] == "Normal: menor que 5,7%"
    assert gme["name"] == "Glicose média estimada (GME)"
    assert gme["is_calculated"] is True
    assert gme["reference_text"] == ""  # a referência impressa é do primeiro exame


def test_label_with_colon_and_no_bar():
    lines = ["CREATININA", "Material: soro", "CREATININA : 1,20 mg/dL"]
    (row,) = extract_inline_block(block(lines))
    assert (row["name"], row["value_text"], row["unit"]) == ("CREATININA", "1,20", "mg/dL")


def test_a_reference_header_after_another_row_belongs_to_that_row():
    # Sem referência para o 1º resultado: a seção impressa depois do 2º é dele.
    lines = [
        "CREATININA",
        "Material: soro",
        "CREATININA: | 1,20 mg/dL",
        "eRFG: | 74 mL/min/1,73 m²",
        "Valores de Referência: eRFG normal: > 90 mL/min/1,73 m²",
    ]
    creatinine, erfg = extract_inline_block(block(lines))
    assert creatinine["reference_text"] == ""
    assert erfg["reference_text"] == "eRFG normal: > 90 mL/min/1,73 m²"


def test_title_line_without_value_is_not_a_row():
    rows = extract_inline_block(block(CREATININE))
    assert "CREATININA" in [r["name"] for r in rows]
    assert [r["source_line"] for r in rows if r["source_line"] == "CREATININA"] == []


# --- o que as regras recusam --------------------------------------------------------------


@pytest.mark.parametrize(
    "value", ["Negativo", "Reagente", "Não reagente", "AMARELA", "Ausente", "Presente"]
)
def test_qualitative_results_go_to_the_llm(value):
    lines = [f"PESQUISA DE ANTICORPOS | {value}", "Material: soro", "Valor de Referência: Negativo"]
    assert extract_inline_block(block(lines)) is None


@pytest.mark.parametrize("cell", ["1,020", "6,0", "12 xyz", "5 a 10 mg/dL"])
def test_numeric_value_without_a_recognised_unit_goes_to_the_llm(cell):
    lines = [f"DENSIDADE | {cell}", "Material: urina"]
    assert extract_inline_block(block(lines)) is None


def test_table_goes_to_the_llm():
    assert looks_like_table(HEMOGRAM)
    assert extract_inline_block(block(HEMOGRAM)) is None


def test_table_with_a_value_in_the_title_line_still_goes_to_the_llm():
    lines = ["PERFIL | 1 mg/dL", "Material: sangue", *HEMOGRAM[4:7]]
    assert looks_like_table(lines)
    assert extract_inline_block(block(lines)) is None


def test_two_table_rows_are_not_a_table_but_the_second_title_shaped_row_is_ambiguous():
    lines = ["PERFIL", "Material: soro", "HDL | 50 mg/dL", "LDL | 100 mg/dL"]
    assert not looks_like_table(lines)
    assert extract_inline_block(block(lines)) is None


def test_two_candidates_for_the_same_exam_conflict():
    lines = ["GLICOSE | 90 mg/dL", "Material: soro", "GLICOSE: | 95 mg/dL"]
    assert extract_inline_block(block(lines)) is None


def test_block_without_any_value_line_is_not_read_by_rules():
    lines = ["OBSERVAÇÕES GERAIS", "Material: soro", "Método: qualquer", "Texto livre."]
    assert extract_inline_block(block(lines)) is None


def test_block_without_a_material_anchor_is_not_read_by_rules():
    # Blocos de reserva (por página) podem ter vários exames: as regras não se arriscam.
    lines = ["GLICOSE | 90 mg/dL", "COLESTEROL | 180 mg/dL", "Valor de Referência: 70 a 99 mg/dL"]
    assert extract_inline_block(Block(title="", lines=lines, page=1)) is None


def test_reference_range_lines_are_not_mistaken_for_results():
    lines = [
        "FSH | 5,0 mIU/mL",
        "Material: soro",
        "Valor de Referência: | Homens...:",
        "Adultos.............: Até 12,0 mIU/mL",
        "Gestantes............: | < 1,0 mIU/mL",
        "Normal: 3 mIU/mL",
        "Anteriores: | 01/01/2020 - 4,0",
    ]
    (row,) = extract_inline_block(block(lines))
    assert row["name"] == "FSH"
    assert "Gestantes" in row["reference_text"]


def test_anteriores_and_collection_date_are_not_results():
    lines = [
        "URÉIA | 30 mg/dL",
        "Material: soro",
        "Data da coleta: | 25/08/2026 - 07:01:51",
        "Anteriores: | 01/01/2020 - 28 | 01/01/2019 - 31 | 01/01/2018 - 29",
    ]
    (row,) = extract_inline_block(block(lines))
    assert row["name"] == "URÉIA"


# --- reference_for ------------------------------------------------------------------------


def test_reference_for_uses_the_first_header_after_the_row():
    b = block(CREATININE)
    assert reference_for(b, 4) == "Homens: 0,70 a 1,30 mg/dL"
    assert reference_for(b, 7) == "eRFG normal: > 90 mL/min/1,73 m²"
    assert reference_for(b, 9) == ""  # nenhuma seção depois da última


def test_reference_for_respects_other_rows_in_between():
    b = block(CREATININE)
    assert reference_for(b, 0, other_rows=[4]) == ""
    assert reference_for(b, 0) == "Homens: 0,70 a 1,30 mg/dL"


def test_reference_for_ignores_table_column_headers_without_a_colon():
    assert reference_for(block(HEMOGRAM), 4) == ""


def test_reference_for_tolerates_pdf_glitches_in_the_header():
    lines = ["X | 1 mg/dL", "Material: soro", "Valoresde Referência : 1 a 2 mg/dL"]
    assert reference_for(block(lines), 0) == "1 a 2 mg/dL"
