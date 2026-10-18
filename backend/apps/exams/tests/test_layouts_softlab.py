"""Regras determinísticas do layout Softl@b (`extract_softlab_block`).

Blocos sintéticos que imitam as formas impressas. Quando as regras não leem o bloco inteiro sem
dúvida, devolvem `None` (nada de meia tabela): o bloco vai ao LLM.
"""

import pytest

from apps.exams.extraction.blocks import Block, split_blocks
from apps.exams.extraction.softlab import extract_softlab_block, simple_reference

from .softlab_fixtures import (
    ALBUMIN,
    BILIRUBIN,
    BIOAVAILABLE_TESTOSTERONE,
    BLEEDING_TIME,
    CHROMIUM,
    COVID,
    CREATININE,
    ESR,
    ESTRADIOL,
    ESTRONE,
    GLUCOSE,
    HBA1C,
    HDL_AFTER_BOTH_ROWS,
    HDL_REFERENCE_BETWEEN_ROWS,
    HEMOGRAM,
    HOMA,
    IGFBP,
    LH,
    POST_PRANDIAL,
    PSA,
    SHBG,
    T3,
    T4,
    TESTOSTERONE,
    TOTAL_CHOLESTEROL_TABLE,
    TOTAL_PROTEIN,
    TSH,
    VDRL,
    VITAMIN_D,
)


def softlab(lines: list[str]) -> Block:
    """O bloco como a segmentação o entrega (sem bordas de tabela, pontilhadas e cabeçalho)."""
    _, blocks = split_blocks([["Softl@b", *lines]])
    assert len(blocks) == 1
    return blocks[0]


def raw(lines: list[str]) -> Block:
    return Block(title=lines[0], lines=lines, page=1, layout="softlab")


def read(lines: list[str]) -> list[dict] | None:
    return extract_softlab_block(softlab(lines))


def slim(rows: list[dict]) -> list[tuple[str, str, str, str]]:
    return [(r["name"], r["value_text"], r["unit"], r["reference_text"]) for r in rows]


# --- resultado único ----------------------------------------------------------------------------


def test_single_result_with_inline_reference_and_the_printed_text_kept():
    rows = read(ALBUMIN)

    assert slim(rows) == [("ALBUMINA", "3.9", "g/dl", "3.2 A 4.8 g/dl")]
    row = rows[0]
    assert row["parse_reference_text"] == "3.2 A 4.8 g/dl"
    assert row["strict_reference"] is True
    assert row["source_line"] == "ALBUMINA | 3.9 g/dl"
    assert row["is_calculated"] is False


def test_previous_results_belong_to_the_row_that_precedes_them():
    rows = read(LH)

    assert rows[0]["previous"] == [["2023-08-02", "3.18"], ["2022-11-11", "5.43"]]


def test_matrix_reference_is_kept_whole_but_never_parsed():
    rows = read(ESTRADIOL)

    assert len(rows) == 1
    assert rows[0]["reference_text"].splitlines() == [
        "HOMENS: PRÉ-PUBERES: 2.0 A 8.0 pg/ml",
        "ADULTOS :10.0 A 35.0 pg/ml",
        "MULHERES: PRÉ-PUBERES: 4.0 A 12.0 pg/ml",
        "FASE FOLICULAR: 5.4 A 214.0 pg/ml",
        "PÓS-MENOPAUSA: 3.0 A 30.0 pg/ml",
    ]
    assert rows[0]["parse_reference_text"] == ""
    assert rows[0]["previous"] == [["2023-08-02", "19.6"]]


def test_reference_stops_at_notes_and_the_collection_line():
    rows = read(TSH)

    assert rows[0]["reference_text"] == "0.4 A 5.50 uUI/ml"
    assert "OBS" not in rows[0]["reference_text"]


def test_sex_pair_with_a_children_line_is_parseable_as_printed():
    rows = read(CREATININE)

    assert rows[0]["reference_text"].splitlines() == [
        "HOMENS : 0.7 A 1.3 mg/dl",
        "MULHERES: 0.5 A 1.1 mg/dl",
        "CRIANÇAS: 0.0 A 0.7 mg/dl",
    ]
    assert rows[0]["parse_reference_text"] == rows[0]["reference_text"]


def test_colon_glued_to_a_number_gets_a_space_only_for_the_reading():
    # `HOMENS:14.5 A 48.4` fazia o parser de referência ler "5 a 48.4"
    rows = read(SHBG)

    assert rows[0]["reference_text"].splitlines() == [
        "HOMENS:14.5 A 48.4 nmol/L",  # como impresso
        "MULHERES(NÃO GRAVIDAS):26.1 A 110.0 nmol/L",
    ]
    assert rows[0]["parse_reference_text"].splitlines() == [
        "HOMENS: 14.5 A 48.4 nmol/L",
        "MULHERES(NÃO GRAVIDAS): 26.1 A 110.0 nmol/L",
    ]


def test_value_and_unit_in_separate_cells_and_unit_spellings_are_kept_as_printed():
    rows = read(TESTOSTERONE)

    assert rows[0]["value_text"] == "512.0"
    assert rows[0]["unit"] == "ng/100ml"
    assert rows[0]["parse_reference_text"] == ""  # matriz por sexo e idade


def test_reference_with_an_average_clause_is_read_up_to_the_range():
    rows = read(T4)

    assert rows[0]["reference_text"] == "0,77 A 2.19 ng/100ml.COM MÉDIA DE 1.23 ng/100ml"
    assert rows[0]["parse_reference_text"] == "0,77 A 2.19 ng/100ml"


def test_header_without_the_material_line_and_with_a_typo_is_still_read():
    rows = read(GLUCOSE)

    assert rows[0]["reference_text"] == "60 - 99 mg/dl"
    assert slim(read(POST_PRANDIAL)) == [
        ("GLICOSE PÓS PRANDIAL", "118.0", "mg/dL", "ATÉ 140,0 mg/dL")
    ]


def test_glued_units_and_a_reference_with_another_unit_spelling_are_not_parsed():
    rows = read(TOTAL_PROTEIN)

    assert (rows[0]["value_text"], rows[0]["unit"]) == ("7.2", "g/dl")
    # a referência imprime mg/dl (erro do laudo): fica só o texto, sem faixa
    assert rows[0]["reference_text"] == "5.7 A 8.2 mg/dl"
    assert rows[0]["parse_reference_text"] == ""


def test_code_like_prefix_is_part_of_the_name():
    rows = read(VITAMIN_D)

    assert rows[0]["name"] == "25 - HIDROXIVITAMINA D"
    assert rows[0]["reference_text"].splitlines()[0].startswith("DEFICIÊNCIA : MENOR QUE 10.0")


def test_name_in_three_cells_and_a_long_age_table_reference():
    rows = read(IGFBP)

    assert rows[0]["name"] == "IGFBP - 3 PROTEINA LIGADORA-3 DO IGF"
    assert (rows[0]["value_text"], rows[0]["unit"]) == ("4,8", "mcg/mL")
    assert rows[0]["parse_reference_text"] == ""
    assert "0,5 A 1,4" in rows[0]["reference_text"]


def test_comma_decimal_and_greek_mu_unit_are_kept_as_printed():
    rows = read(CHROMIUM)

    assert (rows[0]["value_text"], rows[0]["unit"]) == ("0,8", "µg/L")
    assert rows[0]["reference_text"] == "ATÉ 5,0 μg/L"
    assert rows[0]["parse_reference_text"] == "ATÉ 5,0 μg/L"


def test_unit_printed_in_the_name_of_a_row_without_unit_is_taken_from_it():
    rows = read(HBA1C)

    assert rows[0]["name"] == "% HEMOGLOBINA A1C"
    assert rows[0]["unit"] == "%"


def test_estrone_reference_with_a_label_per_line_is_not_parsed():
    rows = read(ESTRONE)

    assert rows[0]["parse_reference_text"] == ""
    assert rows[0]["reference_text"].splitlines()[0] == "- HOMENS : 9.4 A 80.0"


def test_children_reference_with_age_ranges_is_not_parsed():
    rows = read(T3)

    assert (rows[0]["name"], rows[0]["value_text"], rows[0]["unit"]) == ("T3", "121.3", "ng/100ml")
    assert rows[0]["parse_reference_text"] == ""


def test_esr_with_prose_reference_and_uppercase_previous_header():
    rows = read(ESR)

    assert (rows[0]["name"], rows[0]["value_text"], rows[0]["unit"]) == (
        "ERITROSSEDIMENTAÇÃO NA 1ª HORA",
        "6",
        "mm",
    )
    assert rows[0]["previous"] == [["2023-05-10", "4"], ["2023-02-22", "8"]]
    assert rows[0]["parse_reference_text"] == ""


# --- qualitativos ------------------------------------------------------------------------------


def test_qualitative_result_in_the_title_line():
    rows = read(VDRL)

    assert slim(rows) == [("SOROLOGIA PARA LUES - VDRL", "NEGATIVO", "", "")]


def test_panel_that_opens_with_the_reference_gives_it_to_the_first_qualitative_row():
    rows = read(COVID)

    assert [(r["name"], r["value_text"], r["reference_text"]) for r in rows] == [
        ("ANTICORPOS IgG", "NEGATIVO", "NEGATIVO"),
        ("ANTICORPOS IgM", "POSITIVO", ""),
    ]


# --- painéis -----------------------------------------------------------------------------------


def test_reference_after_a_row_belongs_to_that_row_in_a_panel():
    rows = read(PSA)

    assert [(r["name"], r["value_text"], r["reference_text"]) for r in rows] == [
        ("PSA TOTAL", "1.12", "< 4 ng/ml"),
        ("PSA LIVRE", "0.21", ""),
        ("RELAÇÃO PSA LIVRE / PSA TOTAL", "18.7", ""),
    ]
    assert rows[0]["previous"] == [["2023-08-02", "0.94"]]
    assert rows[1]["previous"] == [["2023-08-02", "0.26"]]
    assert "previous" not in rows[2]
    assert rows[0]["parse_reference_text"] == "< 4 ng/ml"


def test_reference_before_the_first_row_belongs_to_the_first_row():
    rows = read(HBA1C)

    assert [(r["name"], r["value_text"], r["unit"], r["reference_text"]) for r in rows] == [
        ("% HEMOGLOBINA A1C", "5.4", "%", "4.0 A 6.5 %"),
        ("GLICEMIA MÉDIA ESTIMADA", "113", "mg/dl", ""),
    ]
    assert rows[0]["previous"] == [["2023-03-22", "5.6"]]
    assert rows[0]["parse_reference_text"] == "4.0 A 6.5 %"


def test_each_row_of_a_panel_takes_the_reference_printed_right_after_it():
    rows = read(BILIRUBIN)

    assert [(r["name"], r["reference_text"]) for r in rows] == [
        ("BILIRRUBINA TOTAL", "0.30 A 1.20 mg/dl."),
        ("BILIRRUBINA DIRETA", "ATÉ 0.30 mg/dl."),
    ]


def test_reference_printed_after_a_row_and_its_percent_variant_belongs_to_the_main_row():
    rows = read(HDL_AFTER_BOTH_ROWS)

    assert [(r["name"], r["value_text"], r["unit"]) for r in rows] == [
        ("COLESTEROL HDL", "49.5", "mg/dL"),
        ("COLESTEROL HDL(%)", "21.3", "%"),
    ]
    assert "> 40" in rows[0]["reference_text"]
    assert rows[1]["reference_text"] == ""
    assert not any(line.startswith("+--") for line in rows[0]["reference_text"].splitlines())


def test_percent_variant_followed_by_its_own_reference():
    rows = read(HDL_REFERENCE_BETWEEN_ROWS)

    assert rows[0]["reference_text"].startswith("BAIXO(RISCO ALTO INDESEJADO) <40 mg/dl")
    assert "MAIOR OU IGUAL A 60 mg/dl" in rows[0]["reference_text"]
    assert rows[1]["name"] == "COLESTEROL HDL(%)"


def test_box_drawn_reference_table_is_kept_and_footnotes_are_not():
    rows = read(TOTAL_CHOLESTEROL_TABLE)

    assert rows[0]["parse_reference_text"] == ""
    assert "DESEJÁVEL" in rows[0]["reference_text"]
    assert "hipercolesterolemia" not in rows[0]["reference_text"]


def test_hemogram_sex_columns_become_labelled_reference_lines():
    rows = {r["name"]: r for r in read(HEMOGRAM)}

    hemacias = rows["HEMÁCIAS"]
    assert (hemacias["value_text"], hemacias["unit"]) == ("5.02", "milhões/ul")
    assert hemacias["reference_text"] == "HOMENS: 4.7 - 6.1\nMULHERES: 4.2 - 5.4"
    assert hemacias["parse_reference_text"] == hemacias["reference_text"]
    # as duas colunas iguais valem uma faixa só para a leitura
    vcm = rows["VCM"]
    assert vcm["unit"] == "u3"
    assert vcm["reference_text"] == "HOMENS: 80.0 - 98.0\nMULHERES: 80.0 - 98.0"
    assert vcm["parse_reference_text"] == "80.0 - 98.0"
    assert rows["HEMÁCIAS"]["section"] == "SÉRIE VERMELHA"
    assert rows["LEUCÓCITOS"]["section"] == "SÉRIE BRANCA"


def test_hemogram_leukogram_percent_and_absolute_rows_with_thousands_dots():
    rows = {r["name"]: r for r in read(HEMOGRAM)}

    assert (rows["LEUCÓCITOS"]["value_text"], rows["LEUCÓCITOS"]["unit"]) == ("7.200", "/mm3")
    assert rows["LEUCÓCITOS"]["reference_text"] == "4.000 - 11.000"
    assert rows["LEUCÓCITOS"]["parse_reference_text"] == "4.000 - 11.000"
    assert (rows["SEGMENTADOS"]["value_text"], rows["SEGMENTADOS"]["unit"]) == ("63", "%")
    assert rows["SEGMENTADOS"]["reference_text"] == "55 - 65"
    absolute = rows["SEGMENTADOS (absoluto)"]
    assert (absolute["value_text"], absolute["unit"]) == ("4.536", "/ mm3")
    assert absolute["reference_text"] == "2.310 - 7.150"
    assert absolute["parse_reference_text"] == "2.310 - 7.150"
    assert rows["BLASTOS"]["reference_text"] == ""
    assert rows["BLASTOS (absoluto)"]["reference_text"] == ""
    assert rows["BASTÕES (absoluto)"]["reference_text"] == "0 - 330"
    assert rows["CONTAGEM DE PLAQUETAS"]["value_text"] == "238.000"
    assert rows["MPV"]["unit"] == "fL"
    assert "NORMOCITOSE E NORMOCROMIA." not in rows  # nota, não resultado
    assert len(rows) == 12


# --- o que vai ao LLM --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("lines", "why"),
    [
        (BIOAVAILABLE_TESTOSTERONE, "linha de resultado com célula a mais"),
        (HOMA, "conversão de unidade entre parênteses numa linha de resultado"),
        (BLEEDING_TIME, "unidade que não é conhecida (min seg)"),
    ],
)
def test_blocks_with_a_row_that_cannot_be_read_go_whole_to_the_llm(lines, why):
    assert read(lines) is None, why


def test_unknown_table_row_in_a_panel_sends_the_whole_panel_to_the_llm():
    lines = [*HEMOGRAM[:12], "ERITROBLASTOS | 2 % | 1 / mm3 | outro | formato", *HEMOGRAM[12:]]

    assert extract_softlab_block(raw(lines)) is None


def test_two_rows_waiting_for_one_reference_go_to_the_llm():
    lines = [
        "PAINEL EXEMPLO",
        "MÉTODO: X",
        "ALFA | 1.0 mg/dl",
        "BETA | 2.0 mg/dl",
        "VALORES DE REFERÊNCIA: 1.0 A 2.0 mg/dl",
        "DATA DA COLETA:12/03/2024",
    ]

    assert read(lines) is None


def test_duplicate_names_in_one_block_go_to_the_llm():
    lines = ["PAINEL EXEMPLO", "ALFA | 1.0 mg/dl", "ALFA | 2.0 mg/dl", "DATA DA COLETA:12/03/2024"]

    assert read(lines) is None


def test_block_that_does_not_start_with_a_title_goes_to_the_llm():
    assert (
        read(["continuação da nota de rodapé do exame anterior", "DATA DA COLETA:12/03/2024"])
        is None
    )


def test_block_of_another_layout_is_not_read():
    block = Block(title="ALBUMINA | 3.9 g/dl", lines=["ALBUMINA | 3.9 g/dl"], page=1)

    assert extract_softlab_block(block) is None


def test_a_row_after_the_collection_line_is_not_read_as_part_of_the_block():
    # a data da coleta fecha o exame; o que vier depois é nota (um novo título abre outro bloco)
    lines = [*ALBUMIN[:-1], "PROTEÍNA | 7.0 g/dl"]

    rows = extract_softlab_block(raw(lines))

    assert [r["name"] for r in rows] == ["ALBUMINA"]


def test_full_split_then_extract_over_a_realistic_page():
    page_lines = [[*ALBUMIN, *ESTRADIOL, *HEMOGRAM, *PSA]]
    _, blocks = split_blocks(page_lines)

    counts = [len(extract_softlab_block(b) or []) for b in blocks]

    assert counts == [1, 1, 12, 3]


# --- referência simples -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("reference", "unit", "expected"),
    [
        ("3.2 A 4.8 g/dl", "g/dl", "3.2 A 4.8 g/dl"),
        ("60 - 99 mg/dl", "mg/dl", "60 - 99 mg/dl"),
        ("30-118 U/L", "U/L", "30-118 U/L"),
        ("0,30 À 0,70 mg/L", "mg/L", "0,30 À 0,70 mg/L"),
        ("< 34 U/L", "U/L", "< 34 U/L"),
        ("NEGATIVO", "", "NEGATIVO"),
        ("NÃO REAGENTE.", "", "NÃO REAGENTE."),
        ("INDETERMINADO", "", ""),
        ("ATÉ 140,0 mg/dL.", "mg/dL", "ATÉ 140,0 mg/dL."),
        ("MENOR QUE 16 IU/ml", "IU/ml", "MENOR QUE 16 IU/ml"),
        ("70 A 130%", "%", "70 A 130%"),
        ("1.9 A 23.0 micro UI/ml", "micro UI/ml", "1.9 A 23.0 micro UI/ml"),
        ("HOMENS: 1.0 A 2.0 mg/dl\nMULHERES: 0.5 A 1.1 mg/dl", "mg/dl", None),
        ("MULHERES: 13.0 A 150.0 ng/ml\nHOMENS: 30.0 A 400.0 ng/ml", "ng/ml", None),
        ("HOMENS 65 - 175 ug/dl\nMULHERES 50 - 170 ug/dl\nCRIANÇAS 40 - 120 ug/dl", "ug/dl", None),
        ("4.0 A 6.5 %", "", "4.0 A 6.5 %"),  # unidade só na referência
        # fora do padrão simples
        ("5.7 A 8.2 mg/dl", "g/dl", ""),  # unidade da referência diferente da do resultado
        ("MENOR QUE 5,7%\nPRÉ-DIABETES - 5,7 A 6,4%", "%", ""),
        ("HOMENS: 1.0 A 2.0 mg/dl\nHOMENS: 1.0 A 2.5 mg/dl", "mg/dl", ""),  # dois "homens"
        ("1.0 A 2.0 mg/dl\n3.0 A 4.0 mg/dl", "mg/dl", ""),  # duas faixas sem rótulo
        ("DESEJÁVEL ... < 200.0 mg/dl\nLIMÍTROFE ... 201 A 239.0 mg/dl", "mg/dl", ""),
        ("HOMENS: 1 A 2\nMULHERES: 1 A 2\nCRIANÇAS: 1 A 2\nIDOSOS: 1 A 2", "mg/dl", ""),
        ("1.234 A 2.345 mg/dl", "mg/dl", ""),  # milhar ou decimal? não adivinhar
        ("", "mg/dl", ""),
    ],
)
def test_simple_reference(reference, unit, expected):
    result = simple_reference(reference, unit)

    assert result == (reference if expected is None else expected)


def test_thousands_dot_is_certain_only_for_counts_per_microliter():
    assert simple_reference("4.000 - 11.000", "/mm3") == "4.000 - 11.000"
    assert simple_reference("4.000 - 11.000", "/ mm3") == "4.000 - 11.000"
    assert simple_reference("4.000 - 11.000", "mg/dl") == ""
    assert simple_reference("0.830 A 15.50", "mUI/ml") == "0.830 A 15.50"  # 0.xxx é decimal


def test_a_range_the_reference_parser_would_misread_is_dropped():
    # sem o espaço depois dos dois-pontos o parser leria "5 a 48.4"; com o espaço lê 14.5-48.4
    assert simple_reference("HOMENS:14.5 A 48.4 nmol/l", "nmol/l") == "HOMENS: 14.5 A 48.4 nmol/l"
    assert simple_reference("HOMENS: 14.5 A 48.4 nmol/l", "nmol/l") == "HOMENS: 14.5 A 48.4 nmol/l"
