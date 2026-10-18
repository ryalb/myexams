"""Gramática de linhas do layout Softl@b: o que é resultado, referência, nota e fim de exame."""

from datetime import date

import pytest

from apps.exams.extraction.softlab_lines import (
    RowLine,
    is_closer,
    is_footnote,
    is_meta,
    is_name,
    is_sex_columns,
    is_title_only,
    looks_like_result,
    parse_previous,
    parse_row,
    reference_header,
    split_cells,
)
from apps.exams.extraction.text import fold


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        # título com resultado e unidade na mesma célula (ponto decimal, ponto final opcional)
        ("ALBUMINA | 3.9 g/dl", RowLine("ALBUMINA", "3.9", "g/dl")),
        ("ESTRADIOL | 23.4 pg/ml.", RowLine("ESTRADIOL", "23.4", "pg/ml")),
        ("LH - ESPECÍFICO | 6.27 mUI/ml", RowLine("LH - ESPECÍFICO", "6.27", "mUI/ml")),
        # valor e unidade em células separadas
        ("AST(TGO) | 31.5 | U/L", RowLine("AST(TGO)", "31.5", "U/L")),
        ("TESTOSTERONA | 512.0 | ng/100ml.", RowLine("TESTOSTERONA", "512.0", "ng/100ml")),
        (
            "CONTAGEM DE PLAQUETAS | 220.000 | /mm3",
            RowLine("CONTAGEM DE PLAQUETAS", "220.000", "/mm3"),
        ),
        # unidade colada ao número
        ("ESTRONA | 88.5pg/ml", RowLine("ESTRONA", "88.5", "pg/ml")),
        ("PROTEÍNAS TOTAIS | 7.2g/dl", RowLine("PROTEÍNAS TOTAIS", "7.2", "g/dl")),
        # o "25 -" faz parte do nome (25-hidroxivitamina D), não é código de exame
        ("25 - HIDROXIVITAMINA D | 36.2 ng/ml", RowLine("25 - HIDROXIVITAMINA D", "36.2", "ng/ml")),
        # nome em duas células
        (
            "TESTOSTERONA LIVRE CALCULADA | (cFT) | 98.0 picog/ml",
            RowLine("TESTOSTERONA LIVRE CALCULADA (cFT)", "98.0", "picog/ml"),
        ),
        ("T3 | 121.3 ng/100ml.", RowLine("T3", "121.3", "ng/100ml")),
        # comparador
        ("PCR | < 0.5 mg/l", RowLine("PCR", "< 0.5", "mg/l")),
        # sem unidade: linha de painel (HbA1c, índices)
        ("% HEMOGLOBINA A1C | 5.4", RowLine("% HEMOGLOBINA A1C", "5.4")),
        ("HOMA BETA | 150.25", RowLine("HOMA BETA", "150.25")),
        # qualitativo: só vocabulário fechado
        (
            "SOROLOGIA PARA LUES - VDRL | NEGATIVO",
            RowLine("SOROLOGIA PARA LUES - VDRL", "NEGATIVO", "", True),
        ),
        ("ANTICORPOS IgG | NÃO REAGENTE", RowLine("ANTICORPOS IgG", "NÃO REAGENTE", "", True)),
        # sem coluna (o vão entre colunas foi pequeno): só com unidade conhecida
        ("ALBUMINA 3.8 g/dl", RowLine("ALBUMINA", "3.8", "g/dl")),
        ("T4 LIVRE 1.33 ng/100ml", RowLine("T4 LIVRE", "1.33", "ng/100ml")),
    ],
)
def test_result_lines_are_read(line, expected):
    assert parse_row(line) == expected


@pytest.mark.parametrize(
    "line",
    [
        "BLASTOS | 0 % | 0 / mm3",  # duas grandezas: é linha de tabela, não de resultado único
        "TESTOSTERONA BIODISPONÍVEL(BaT) | 301.0 | ng/dl | 61.0 %",  # célula a mais
        "GLICEMIA JEJUM | 5.10 | mmol/L (92 | mg/dl)",  # conversão entre parênteses
        "TEMPO DE SANGRIA | 2 min 10 seg",  # unidade desconhecida
        "ADULTOS | :10.0 A 35.0 pg/ml",  # faixa de referência
        "PÓS-MENOPAUSA: | 3.0 A 30.0 pg/ml",  # rótulo com dois-pontos
        "MUITO ALTO | < 7 | < 12",
        "ANTICORPOS IgG | EM ANÁLISE",  # qualitativo fora do vocabulário
        # frases com cara de resultado quando não há coluna
        "ESSES VALORES SITUAM-SE ENTRE 30.0 E 90.0 ng/ml",
        "ALTO (RISCO BAIXO DESEJADO) MAIOR OU IGUAL A 60 mg/dl",
        "HOMEM ADULTO:250,0 A 800,0 ng/100ml",
        "< 1 ANO 12,0 A 21,0 ng/100ml.",
        "Resultado do exame 3.9 g/dl",  # texto corrido em minúsculas
        "",
    ],
)
def test_lines_that_are_not_results_are_not_read(line):
    assert parse_row(line) is None


@pytest.mark.parametrize(
    ("name", "ok"),
    [
        ("HEMOGLOBINA GLICADA", True),
        ("T3", True),
        ("COLESTEROL HDL(%)", True),
        ("ANTICORPOS IgG", True),  # "g" minúsculo isolado não é palavra
        ("25 - HIDROXIVITAMINA D3", True),
        ("HEMÁCIAS.....:", False),
        ("PÓS-MENOPAUSA:", False),
        ("Hemoglobina", False),
        ("1 A 7 DIAS", False),
        ("X", False),
    ],
)
def test_names(name, ok):
    assert is_name(name) is ok


def test_title_only_lines_and_meta_lines():
    assert is_title_only("HEMOGRAMA")
    assert is_title_only("PSA TOTAL / PSA LIVRE")
    assert is_title_only("TESTE RÁPIDO PARA COVID-19")
    assert not is_title_only("ALBUMINA | 3.9 g/dl")
    assert not is_title_only("MÉTODO:COLORIMÉTRICO")
    assert not is_title_only("Normocitose e normocromia.")

    for line in (
        "MÉTODO:COLORIMÉTRICO",
        "METODO:ELETROQUIMIOLUMINESCÊNCIA",
        "MATERIAL: SORO",
        "REGISTRO ANVISA : MS 000",
        "DATA LIBERAÇÃO:12/03/2024",
        "Resultados Anteriores:19.6[02/08/2023];",
        "OBS : NOVO MÉTODO",
        "NOTA : OS NÍVEIS",
        "ATENÇÃO PARA NOVOS VALORES DE REFERÊNCIA",
        "NOVOS VALORES DE REFERÊNCIA A PARTIR DE DEZEMBRO 2014.",
    ):
        assert is_meta(fold(line)), line
    assert not is_meta(fold("ALBUMINA | 3.9 g/dl"))
    assert is_closer(fold("DATA DA COLETA:12/03/2024 | DATA LIBERAÇÃO:12/03/2024"))
    assert is_closer(fold("DATA DA LIBERAÇÃO:12/03/2024"))
    assert not is_closer(fold("OBS: DATA DA COLETA"))


def test_footnotes_are_told_apart_from_box_drawn_table_rows():
    assert is_footnote("* Em adultos o colesterol Total >310 mg/dL")
    assert is_footnote("*** Quando os níveis de triglicérides")
    assert not is_footnote("* | < 190 | * | < 190 | * | DESEJÁVEL | *")
    assert not is_footnote("*> OU = 19 ANOS:45-115 U/L *46-50 ANOS:39-100 U/L")
    assert not is_footnote("CRIANÇAS : ********* MASCULINOS******")


@pytest.mark.parametrize(
    ("line", "rest"),
    [
        ("VALORES DE REFERÊNCIA:3.2 A 4.8 g/dl", "3.2 A 4.8 g/dl"),
        (
            "VALORES DE REFERÊNCIA*: DEFICIÊNCIA : MENOR QUE 10.0 ng/ml",
            "DEFICIÊNCIA : MENOR QUE 10.0 ng/ml",
        ),
        ("VALORES DE REFERÊCNIA: 0,77 A 2.19 ng/100ml", "0,77 A 2.19 ng/100ml"),  # erro de grafia
        ("VALORES DE REFEFRÊNCIA : | ATÉ 150 mg/dL", "| ATÉ 150 mg/dL"),
        ("VALORES REFERÊNCIA: ADULTOS :46 A 116 U/L", "ADULTOS :46 A 116 U/L"),
        ("VALOR DE REFERENCIA: ATÉ 5,0 μg/L", "ATÉ 5,0 μg/L"),
        ("VALORES DE REFERÊNCIA", ""),
        ("VALORES DE REFERÊNCIA :", ""),
    ],
)
def test_reference_header_variants(line, rest):
    assert reference_header(line) == rest


def test_reference_header_is_only_at_the_start_of_the_line():
    assert reference_header("SÉRIE VERMELHA | VALORES DE REFERÊNCIA") is None
    assert reference_header("ATENÇÃO PARA NOVOS VALORES DE REFERÊNCIA") is None
    assert reference_header("ALBUMINA | 3.9 g/dl") is None


def test_previous_results_with_one_or_many_dates_and_a_pair_cut_by_the_page_edge():
    assert parse_previous("Resultados Anteriores:19.6[02/08/2023];") == [(date(2023, 8, 2), "19.6")]
    assert parse_previous("Resultados Anteriores:1.72[14/02/2023]; 2.18[23/08/2022]; 1.55[1") == [
        (date(2023, 2, 14), "1.72"),
        (date(2022, 8, 23), "2.18"),
    ]
    assert parse_previous("RESULTADOS ANTERIORES :4[10/05/2023];8[22/02/2023];") == [
        (date(2023, 5, 10), "4"),
        (date(2023, 2, 22), "8"),
    ]
    assert parse_previous("Resultados Anteriores:1.5[31/02/2023];") == []  # data inexistente
    assert parse_previous("DATA DA COLETA:12/03/2024") is None


def test_columns_header_and_cells():
    assert is_sex_columns("HOMENS | MULHERES")
    assert not is_sex_columns("SÉRIE VERMELHA | VALORES DE REFERÊNCIA")
    assert split_cells("MPV | 10.8 fL | 7.0 - 10.0") == ["MPV", "10.8 fL", "7.0 - 10.0"]


def test_result_looking_lines_that_cannot_be_read_are_detected():
    # fora da referência, qualquer segunda célula numérica sem comparador é suspeita
    assert looks_like_result(
        ["TESTOSTERONA BIODISPONÍVEL(BaT)", "301.0", "ng/dl", "61.0 %"], in_reference=False
    )
    assert looks_like_result(["TEMPO DE SANGRIA", "2 min 10 seg"], in_reference=False)
    assert not looks_like_result(["SÉRIE VERMELHA", "VALORES DE REFERÊNCIA"], in_reference=False)
    assert not looks_like_result(["MUITO ALTO", "< 7", "< 12"], in_reference=False)
    # dentro da referência, faixas por idade/sexo são texto de referência
    assert not looks_like_result(["ALTO", "7 A 15", "12 A 18"], in_reference=True)
    assert not looks_like_result(["TÓXICO", ": MAIOR QUE 100.0 ng/ml"], in_reference=True)
    assert looks_like_result(["GLICEMIA JEJUM", "5.10", "mmol/L (92", "mg/dl)"], in_reference=True)
