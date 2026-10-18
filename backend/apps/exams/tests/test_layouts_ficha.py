"""Regras determinísticas do layout "ficha" (`extract_ficha_block`).

Blocos sintéticos que imitam as formas impressas por esse laboratório. Quando as regras não
leem o bloco inteiro sem dúvida, devolvem `None` (nada de meia tabela): o bloco vai ao LLM.
"""

from apps.exams.extraction.blocks import Block
from apps.exams.extraction.layouts import extract_ficha_block

from .ficha_fixtures import HEMOGRAM, received


def ficha(*lines: str) -> Block:
    kept = [
        line
        for line in lines
        if not line.startswith(("Assinatura digital", "Exame liberado de acordo"))
    ]
    return Block(title=kept[0], lines=kept, page=1, layout="ficha")


def single(title: str, *rest: str) -> Block:
    return ficha(title, "Método: Ensaio", "RESULTADO | VALORES DE REFERÊNCIA", *rest, *received())


def slim(rows: list[dict]) -> list[tuple[str, str, str, str]]:
    return [(r["name"], r["value_text"], r["unit"], r["reference_text"]) for r in rows]


# --- exame de resultado único ------------------------------------------------------------------


def test_single_result_with_multiline_age_and_fasting_reference():
    rows = extract_ficha_block(
        single(
            "COLESTEROL TOTAL, soro",
            "171 mg/dL | Maior ou igual a 20 anos:",
            "Com ou sem jejum de 12 horas:",
            "Desejável: menor que 190 mg/dL.",
            "NOTA: Referências:",
            "Atualização da Diretriz Brasileira de Dislipidemias e Prevenção da",
        )
    )

    assert slim(rows) == [
        (
            "COLESTEROL TOTAL",
            "171",
            "mg/dL",
            "Maior ou igual a 20 anos: Com ou sem jejum de 12 horas: "
            "Desejável: menor que 190 mg/dL.",
        )
    ]
    row = rows[0]
    assert row["source_line"] == "171 mg/dL | Maior ou igual a 20 anos:"
    assert row["printed_flag"] == "" and row["is_calculated"] is False
    assert row["strict_reference"] is True
    assert row["section"] == ""
    assert row["parse_reference_text"] == row["reference_text"]


def test_single_result_with_inline_reference():
    rows = extract_ficha_block(single("UREIA, soro", "27 mg/dL | 10 a 50 mg/dL"))
    assert slim(rows) == [("UREIA", "27", "mg/dL", "10 a 50 mg/dL")]


def test_title_without_specimen_keeps_the_whole_title_and_second_title_line_is_ignored():
    block = ficha(
        "TRANSAMINASE GLUTAMICO-OXALACETICA (TGO)",
        "ASPARTATO AMINO TRANSFERASE (AST), Atividade no soro",
        "Método: Cinético, UV",
        "RESULTADO | VALORES DE REFERÊNCIA",
        "29 U/L | maior que 2 anos: até 40 U/L",
        *received(),
    )

    assert slim(extract_ficha_block(block)) == [
        ("TRANSAMINASE GLUTAMICO-OXALACETICA (TGO)", "29", "U/L", "maior que 2 anos: até 40 U/L")
    ]


def test_value_line_with_comparator_phrase_keeps_the_phrase_in_the_value_text():
    rows = extract_ficha_block(
        single(
            "LÍTIO, soro",
            "inferior a 0,15 mEq/L | NÍVEL TERAPÊUTICO: 0,60 a 1,20 mEq/L",
            "NÍVEL TÓXICO | : acima de 1,50 mEq/L",
            "Limite de quantificação: 0,15 mEq/L",
        )
    )

    assert [(r["value_text"], r["unit"]) for r in rows] == [("inferior a 0,15", "mEq/L")]
    assert rows[0]["reference_text"].startswith("NÍVEL TERAPÊUTICO: 0,60 a 1,20 mEq/L")
    assert "Limite de quantificação" not in rows[0]["reference_text"]


def test_value_alone_under_result_header_takes_the_reference_after_its_own_header():
    block = ficha(
        "HEMOGLOBINA GLICADA (A1C), sangue total",
        "Método: Imunoensaio",
        "RESULTADO",
        "6,0 %",
        "Esse valor de hemoglobina glicada corresponde à glicemia média estimada",
        "de 126 mg/dL.",
        "VALORES DE REFERÊNCIA",
        "Indivíduos sem diabetes mellitus:",
        "Menor que 5,7% - baixo risco de desenvolvimento de diabetes",
        "RESULTADOS ANTERIORES:",
        "FICHA | DATA | RESULTADO",
        "========== | ========== | =========",
        "9990000002 | 19/08/2023 | 5,9",
        *received(),
    )

    rows = extract_ficha_block(block)

    assert [(r["name"], r["value_text"], r["unit"]) for r in rows] == [
        ("HEMOGLOBINA GLICADA (A1C)", "6,0", "%")
    ]
    reference = rows[0]["reference_text"]
    assert reference.startswith("Indivíduos sem diabetes mellitus: Menor que 5,7%")
    assert "glicemia média" not in reference and "ANTERIORES" not in reference
    assert rows[0]["source_line"] == "6,0 %"


def test_calculated_exams_are_flagged():
    block = ficha(
        "LDL-COLESTEROL, soro",
        "Método: Cálculo baseado na fórmula de Martin/Hopkins",
        "RESULTADO | VALORES DE REFERÊNCIA",
        "118 mg/dL | menor que 130 mg/dL",
        *received(),
    )
    assert extract_ficha_block(block)[0]["is_calculated"] is True


def test_formula_line_without_method_label_also_marks_calculated():
    block = ficha(
        "VLDL-COLESTEROL, soro",
        "Fórmula de Martin/Hopkins",
        "RESULTADO | VALORES DE REFERÊNCIA",
        "25 mg/dL | menor que 30 mg/dL",
        *received(),
    )
    assert extract_ficha_block(block)[0]["is_calculated"] is True


# --- referência: só se não houver ambiguidade --------------------------------------------------


def test_reference_matrix_printed_before_the_value_goes_to_the_llm():
    rows = extract_ficha_block(
        ficha(
            "GLOBULINA LIGADORA DE HORMONIOS SEXUAIS (SHBG), soro",
            "RESULTADO | VALORES DE REFERÊNCIA ( nmol/L )",
            "IDADE: | FEMININO: | MASCULINO:",
            "Adultos:",
            "40 nmol/L | Até 49 anos : | 32 ­ 128 | 18 ­ 54",
            "Acima 50 anos: | 27 - 128 | 21 - 77",
            *received(),
        )
    )

    assert rows is None


def test_matrix_reference_after_the_value_line_is_not_parsed():
    rows = extract_ficha_block(
        single(
            "HORMÔNIO EXEMPLO, soro",
            "40 nmol/L | FEMININO: | MASCULINO:",
            "Até 49 anos : | 32 ­ 128 | 18 ­ 54",
        )
    )

    assert rows[0]["reference_text"] != ""
    assert rows[0]["parse_reference_text"] == ""


def test_long_prose_reference_is_kept_raw_but_not_parsed():
    prose = [
        "VALORES DE REFERÊNCIA",
        "O exame de proteína C-reativa (PCR) pode ser utilizado para avaliação",
        "das seguintes condições clínicas:",
        "Como indicador de processos infecciosos ou inflamatórios: valores acima de 0,3 mg/dL",
        "Como indicador de risco cardiovascular: abaixo de 0,1 mg/dL baixo risco, entre 0,1 e",
        "0,3 mg/dL risco médio e acima de 0,3 mg/dL alto risco, em pacientes sem infecção.",
    ]
    rows = extract_ficha_block(
        ficha("PROTEINA C-REATIVA, soro", "RESULTADO", "0,07 mg/dL", *prose, *received())
    )

    assert rows[0]["value_text"] == "0,07"
    assert rows[0]["reference_text"].startswith("O exame de proteína C-reativa")
    assert rows[0]["parse_reference_text"] == ""


def test_sex_split_reference_stays_parseable():
    rows = extract_ficha_block(
        single(
            "TESTOSTERONA LIVRE, soro",
            "512,3 pmol/L | Homens : 131 a 640 pmol/L",
            "Mulheres: 2,4 a 37,0 pmol/L",
            "Cálculo baseado nos níveis de Testosterona Total e SHBG, segundo",
            "TESTOSTERONA: 590 ng/dL",
            "GLOBULINA LIGADORA DE HORMONIOS SEXUAIS (SHBG): 40 nmol/L",
        )
    )

    assert [(r["name"], r["value_text"], r["unit"]) for r in rows] == [
        ("TESTOSTERONA LIVRE", "512,3", "pmol/L")
    ]
    assert rows[0]["reference_text"] == "Homens : 131 a 640 pmol/L Mulheres: 2,4 a 37,0 pmol/L"
    assert rows[0]["parse_reference_text"] == rows[0]["reference_text"]


# --- blocos que as regras não leem -------------------------------------------------------------


def test_block_with_a_second_result_section_goes_to_the_llm():
    block = ficha(
        "CREATININA, soro",
        "Método: Cinético colorimétrico",
        "RESULTADO | VALOR DE REFERÊNCIA",
        "0,95 mg/dL | Acima de 12 anos: 0,70 a 1,30",
        "ESTIMATIVA DA FILTRAÇÃO GLOMERULAR | =================================",
        "Método: Cálculo da estimativa",
        "RESULTADO | VALOR DE REFERÊNCIA",
        "mL/min/1,73 m2 | mL/min/1,73 m2",
        "CKD-EPI 2009",
        *received(),
    )
    assert extract_ficha_block(block) is None


def test_qualitative_result_goes_to_the_llm():
    assert extract_ficha_block(single("HEPATITE B, soro", "Não reagente | Não reagente")) is None


def test_block_without_a_result_header_goes_to_the_llm():
    block = ficha("EXAME SEM TABELA", "Método: Algum", "171 mg/dL", *received())
    assert extract_ficha_block(block) is None


def test_value_line_without_unit_or_label_is_not_guessed_when_the_line_is_text():
    block = single("EXAME ESTRANHO", "Ver observação abaixo | 10 a 20")
    assert extract_ficha_block(block) is None


def test_old_layout_blocks_are_not_touched():
    old = Block(title="GLICOSE", lines=["GLICOSE | 103 mg/dL", "Material: soro"], page=1)
    assert extract_ficha_block(old) is None


def test_note_sentences_starting_with_result_words_are_not_column_headers():
    rows = extract_ficha_block(
        single(
            "TIROXINA (T4) LIVRE, soro",
            "1,4 ng/dL | Acima de 20 anos: 0,9 a 1,7 ng/dL",
            "NOTA(1): Os valores de referência foram validados a partir",
            "da análise de amostras, com dosagens dentro dos",
            "valores de referência para a idade e anticorpos negativos.",
            "NOTA(2): O resultado deve ser avaliado em conjunto com dados clínicos.",
            "resultado deve ser preferencialmente avaliado em conjunto com",
        )
    )

    assert slim(rows) == [
        ("TIROXINA (T4) LIVRE", "1,4", "ng/dL", "Acima de 20 anos: 0,9 a 1,7 ng/dL")
    ]


def test_age_heading_and_sex_lines_are_never_read_as_results():
    block = ficha(
        "HEMOSSEDIMENTACAO, sangue total",
        "RESULTADO | VALORES DE REFERÊNCIA",
        "TÉCNICA AUTOMATIZADA: | 18 a 65 anos",
        "PRIMEIRA HORA | : 9 mm | Masculino: 2 a 28 mm",
        "Feminino : 2 a 36 mm",
        *received(),
    )
    # "18" é a ponta de uma faixa de idade e "Feminino" é rótulo de referência: tabela ambígua.
    assert extract_ficha_block(block) is None


def test_age_qualifier_line_before_labelled_rows_is_a_population_not_a_result():
    rows = extract_ficha_block(
        ficha(
            "SATURAÇÃO DA TRANSFERRINA, soro",
            "Método: Colorimétrico",
            "RESULTADO | VALORES DE REFERÊNCIA",
            "Acima de 12 anos:",
            "Ferro | : 112 mcg/dL | 65 a 175 mcg/dL",
            "Saturação da transferrina : 36 % | 20 a 50 %",
            *received(),
        )
    )

    assert slim(rows) == [
        ("Ferro", "112", "mcg/dL", "Acima de 12 anos: 65 a 175 mcg/dL"),
        ("Saturação da transferrina", "36", "%", "Acima de 12 anos: 20 a 50 %"),
    ]


def test_comparator_phrase_followed_by_an_age_is_a_population_for_the_single_result():
    rows = extract_ficha_block(
        single("EXAME POR IDADE, soro", "Acima de 12 anos:", "0,5 mg/dL | 0,3 a 0,9 mg/dL")
    )

    assert slim(rows) == [("EXAME POR IDADE", "0,5", "mg/dL", "Acima de 12 anos: 0,3 a 0,9 mg/dL")]


# --- vários resultados rotulados (PSA, bilirrubinas) -------------------------------------------


def test_labelled_rows_with_and_without_unit():
    rows = extract_ficha_block(
        ficha(
            "ANTIGENO PROSTATICO ESPECIFICO (PSA), soro",
            "Método: Eletroquimioluminométrico",
            "RESULTADO | VALOR DE REFERÊNCIA",
            "PSA Total: 0,81 ng/mL | vide nota 1",
            "PSA Livre: 0,21 ng/mL | vide nota 2",
            "Relacao PSA livre/PSA total: 0,18 | vide nota 2",
            "Ensaio ultra­sensível, limite de detecção: PSA total = 0,007 ng/mL",
            "NOTA 1:",
            "­ O PSA total deve ser interpretado considerando vários fatores:",
            *received(),
        )
    )

    assert slim(rows) == [
        ("PSA Total", "0,81", "ng/mL", "vide nota 1"),
        ("PSA Livre", "0,21", "ng/mL", "vide nota 2"),
        ("Relacao PSA livre/PSA total", "0,18", "", "vide nota 2"),
    ]
    assert rows[0]["source_line"] == "PSA Total: 0,81 ng/mL | vide nota 1"


def test_generic_row_labels_are_prefixed_with_the_exam_name():
    rows = extract_ficha_block(
        ficha(
            "BILIRRUBINAS, soro",
            "RESULTADO | VALORES DE REFERÊNCIA",
            "DIRETA : 0,45 mg/dL | 0,00 a 0,30 mg/dL",
            "INDIRETA: 0,51 mg/dL | 0,20 a 0,80 mg/dL",
            "TOTAL | : 0,88 mg/dL | 0,20 a 1,10 mg/dL",
            *received(),
        )
    )

    assert [(r["name"], r["value_text"]) for r in rows] == [
        ("BILIRRUBINAS DIRETA", "0,45"),
        ("BILIRRUBINAS INDIRETA", "0,51"),
        ("BILIRRUBINAS TOTAL", "0,88"),
    ]
    assert rows[2]["reference_text"] == "0,20 a 1,10 mg/dL"


def test_row_like_line_that_is_not_a_result_sends_the_whole_block_to_the_llm():
    rows = extract_ficha_block(
        ficha(
            "PERFIL, soro",
            "RESULTADO | VALORES DE REFERÊNCIA",
            "SODIO: 139 mEq/L | 136 a 145 mEq/L",
            "Normal: 70 a 99 mg/dL",
            *received(),
        )
    )
    # "Normal: 70..." parece linha de resultado, mas "Normal" não é nome de exame: nunca
    # se emite meia tabela.
    assert rows is None


def test_row_after_trailing_prose_is_ambiguous_and_goes_to_the_llm():
    rows = extract_ficha_block(
        ficha(
            "PERFIL, soro",
            "RESULTADO | VALORES DE REFERÊNCIA",
            "SODIO: 139 mEq/L | 136 a 145 mEq/L",
            "uma observação em texto corrido, minúscula",
            "POTASSIO: 4,0 mEq/L | 3,5 a 5,1 mEq/L",
            *received(),
        )
    )
    assert rows is None


def test_unknown_text_before_the_first_row_sends_the_block_to_the_llm():
    rows = extract_ficha_block(
        ficha(
            "PERFIL, soro",
            "RESULTADO | VALORES DE REFERÊNCIA",
            "observação em texto corrido, minúscula",
            "SODIO: 139 mEq/L | 136 a 145 mEq/L",
            *received(),
        )
    )
    assert rows is None


# --- hemograma ---------------------------------------------------------------------------------


def hemogram() -> Block:
    return ficha(*HEMOGRAM)


POP = "Masc: Maior ou igual a 18 anos"


def test_hemogram_rows_with_wrapped_names_sections_and_population_reference():
    rows = extract_ficha_block(hemogram())

    assert slim(rows) == [
        ("ERITRÓCITOS", "4,95", "milhões/mm3", f"{POP}: 4,32 a 5,67"),
        ("HEMOGLOBINA", "14,8", "g/dL", f"{POP}: 13,3 a 16,5"),
        ("HEMATÓCRITO", "43,1", "%", f"{POP}: 39,2 a 49,0"),
        (
            "CONCENTRAÇÃO DE HEMOGLOBINA CORPUSCULAR MÉDIA",
            "33,9",
            "g/dL",
            f"{POP}: 32,4 a 36,0",
        ),
        (
            "COEFICIENTE DE VARIAÇÃO DO VOLUME ERITROCITÁRIO (RDW)",
            "13,4",
            "%",
            f"{POP}: 11,8 a 14,1",
        ),
        ("LEUCÓCITOS", "6.210", "/mm3", f"{POP}: 3.650 a 8.120"),
        ("Neutrófilos", "58,3", "%", ""),
        ("Neutrófilos (absoluto)", "3.620", "/mm3", f"{POP}: 1.590 a 4.770"),
        ("Eosinófilos", "3,1", "%", ""),
        ("Eosinófilos (absoluto)", "190", "/mm3", f"{POP}: 34 a 420"),
        ("Linfócitos", "30,4", "%", ""),
        ("Linfócitos (absoluto)", "1.890", "/mm3", f"{POP}: 1.120 a 2.950"),
        ("TOTAL DE PLAQUETAS", "231.000", "/mm3", f"{POP}: 151.000 a 304.000/mm3"),
        ("VOLUME PLAQUETÁRIO MÉDIO", "10,7", "fL", f"{POP}: 9,2 a 12,6 fL"),
    ]
    assert all(r["strict_reference"] for r in rows)


def test_hemogram_sections_and_source_lines():
    rows = extract_ficha_block(hemogram())
    by_name = {r["name"]: r for r in rows}

    assert by_name["ERITRÓCITOS"]["section"] == ""
    assert by_name["LEUCÓCITOS"]["section"] == "SÉRIE BRANCA"
    assert by_name["Neutrófilos"]["section"] == "SÉRIE BRANCA"
    assert by_name["TOTAL DE PLAQUETAS"]["section"] == "PLAQUETAS"
    assert by_name["Neutrófilos"]["source_line"] == "Neutrófilos | : 58,3 | 3.620 | 1.590 a 4.770"
    assert by_name["Neutrófilos (absoluto)"]["source_line"] == by_name["Neutrófilos"]["source_line"]
    # Linha com nome quebrado: a linha de origem é a que traz o valor.
    assert by_name["CONCENTRAÇÃO DE HEMOGLOBINA CORPUSCULAR MÉDIA"]["source_line"] == (
        "CORPUSCULAR MÉDIA: 33,9 g/dL | 32,4 a 36,0"
    )


def test_percent_row_never_gets_the_absolute_reference():
    rows = extract_ficha_block(hemogram())
    percents = [r for r in rows if r["unit"] == "%" and r["name"].startswith(("Neutr", "Eosin"))]
    assert percents and all(r["reference_text"] == "" for r in percents)


def test_platelet_section_with_an_unlabelled_value_takes_the_section_name():
    block = ficha(
        "HEMOGRAMA, sangue total",
        "VALORES DE REFERÊNCIA",
        "RESULTADO | Masc: Maior ou igual a 18 anos",
        "ERITRÓCITOS | : | 5,12 milhões/mm3 | 4,32 a 5,67",
        "PLAQUETAS | ===========================================================",
        "RESULTADO | VALORES DE REFERÊNCIA",
        "244.000/mm3 | 151.000 a 304.000/mm3",
        "NOTA(1): Os valores de referência para adultos são baseados em um",
        *received(),
    )

    rows = extract_ficha_block(block)

    assert slim(rows)[-1] == ("PLAQUETAS", "244.000", "/mm3", "151.000 a 304.000/mm3")
    assert rows[-1]["section"] == "PLAQUETAS"


def test_leukogram_row_with_unreadable_tail_sends_the_block_to_the_llm():
    lines = [
        line.replace("Eosinófilos | : | 3,1 | 190 | 34 a 420", "Eosinófilos | : | 2,2 | x")
        for line in HEMOGRAM
    ]
    assert extract_ficha_block(ficha(*lines)) is None


def test_leukogram_single_value_without_a_decidable_column_goes_to_the_llm():
    lines = [line.replace("6.210 | 3.650 a 8.120", "55 | 30 a 80") for line in HEMOGRAM]
    # "55": sem unidade impressa e referência só com inteiros pequenos: coluna indecidível.
    assert extract_ficha_block(ficha(*lines)) is None
