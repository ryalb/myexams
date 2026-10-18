"""Segmentação do layout Softl@b: exames separados por pontilhada, por `DATA DA COLETA:` ou só
pelo título seguinte; painéis (hemograma, PSA) numa peça só."""

from apps.exams.extraction.blocks import split_blocks, strip_boilerplate
from apps.exams.extraction.linearize import PageText

from .softlab_fixtures import (
    ALBUMIN,
    BILIRUBIN,
    COVID,
    ESTRADIOL,
    GLUCOSE,
    HDL_AFTER_BOTH_ROWS,
    HDL_REFERENCE_BETWEEN_ROWS,
    HEMOGRAM,
    ISSUED,
    LH,
    ORDER,
    PATIENT,
    POST_PRANDIAL,
    PSA,
    SEPARATOR,
    TOTAL_CHOLESTEROL_TABLE,
    TSH,
    VITAMIN_D,
    header_lines,
    pages,
)


def blocks_of(page_list: list[PageText]):
    cleaned, header = strip_boilerplate(page_list)
    preface, blocks = split_blocks(cleaned)
    return header, preface, blocks


def titles(blocks) -> list[str]:
    return [block.title for block in blocks]


def test_one_block_per_exam_separated_by_the_long_dotted_line():
    _, _, blocks = blocks_of(pages(ALBUMIN + ESTRADIOL + LH))

    assert titles(blocks) == [
        "ALBUMINA | 3.9 g/dl",
        "ESTRADIOL | 23.4 pg/ml.",
        "LH - ESPECÍFICO | 6.27 mUI/ml",
    ]
    assert {b.layout for b in blocks} == {"softlab"}
    albumin, estradiol, _ = blocks
    assert albumin.method == "COLORIMÉTRICO - EXEMPLO"
    assert albumin.specimen == "SORO"
    assert albumin.collected_at == "12/03/2024"
    assert estradiol.collected_at == "12/03/2024 07:32:00"
    assert not any(SEPARATOR in b.lines for b in blocks)


def test_without_dotted_lines_the_next_title_with_a_unit_starts_the_next_exam():
    _, _, blocks = blocks_of(pages(GLUCOSE[:-1] + POST_PRANDIAL[:-1]))

    assert titles(blocks) == [
        "GLICOSE (GLICEMIA JEJUM) | 89.0 mg/dl",
        "GLICOSE PÓS PRANDIAL | 118.0 mg/dL",
    ]
    assert blocks[0].lines[-1] == "ATENÇÃO PARA NOVOS VALORES DE REFERÊNCIA"


def test_each_page_starts_a_new_block_and_blocks_never_span_pages():
    _, _, blocks = blocks_of(pages(ALBUMIN[:-1], TSH[:-1]))

    assert titles(blocks) == ["ALBUMINA | 3.9 g/dl", "TSH ULTRA SENSÍVEL | 2.31 uUI/ml."]
    assert [b.page for b in blocks] == [1, 2]


def test_a_title_with_a_unit_after_the_collection_date_starts_a_new_block():
    # a data da coleta fecha o exame: o resto até o próximo título é nota
    body = [*ALBUMIN[:5], "OBS: nota livre do laboratório.", *TSH[:-1]]
    _, _, blocks = blocks_of(pages(body))

    assert titles(blocks) == ["ALBUMINA | 3.9 g/dl", "TSH ULTRA SENSÍVEL | 2.31 uUI/ml."]
    assert "OBS: nota livre do laboratório." in blocks[0].lines


def test_prose_that_looks_like_a_title_after_the_collection_date_is_a_note():
    _, _, blocks = blocks_of(pages(VITAMIN_D))

    assert len(blocks) == 1
    assert "ESSES VALORES SITUAM-SE ENTRE 30.0 E 90.0 ng/ml" in blocks[0].lines


def test_panel_swallows_its_result_rows_until_closed():
    _, _, blocks = blocks_of(pages(HEMOGRAM + PSA))

    assert titles(blocks) == ["HEMOGRAMA", "PSA TOTAL / PSA LIVRE"]
    psa = blocks[1]
    assert "PSA LIVRE | 0.21 ng/mL" in psa.lines
    assert psa.lines[-1] == "total são superiores a 4.0 ng / ml."  # notas depois da coleta ficam
    assert blocks[0].collected_at == "12/03/2024 08:14:00"


def test_title_only_line_followed_by_method_opens_a_panel_even_without_a_closer():
    body = [*TSH[:-1], *BILIRUBIN]
    _, _, blocks = blocks_of(pages(body))

    assert titles(blocks) == ["TSH ULTRA SENSÍVEL | 2.31 uUI/ml.", "BILIRRUBINA TOTAL E FRAÇÕES"]
    assert "BILIRRUBINA DIRETA | 0.2 mg/dl." in blocks[1].lines


def test_percent_variant_stays_in_the_block_of_the_main_result():
    _, _, blocks = blocks_of(pages(HDL_AFTER_BOTH_ROWS + TSH[:-1]))

    assert titles(blocks) == ["COLESTEROL HDL | 49.5 mg/dL", "TSH ULTRA SENSÍVEL | 2.31 uUI/ml."]
    assert "COLESTEROL HDL(%) | 21.3 %" in blocks[0].lines


def test_inside_a_reference_only_a_piped_line_with_a_unit_starts_an_exam():
    # `ALTO ... MAIOR OU IGUAL A 60 mg/dl` (sem coluna) é faixa; `COLESTEROL HDL(%) | 19.8 %` não
    # abre exame por ser variante em percentual; o `TSH` seguinte (em colunas) abre.
    _, _, blocks = blocks_of(pages(HDL_REFERENCE_BETWEEN_ROWS[:-1] + TSH[:-1]))

    assert titles(blocks) == ["COLESTEROL HDL | 51.0 mg/dl", "TSH ULTRA SENSÍVEL | 2.31 uUI/ml."]
    assert "ALTO (RISCO BAIXO DESEJADO) MAIOR OU IGUAL A 60 mg/dl" in blocks[0].lines


def test_short_dotted_lines_and_table_borders_are_dropped_not_separators():
    body = [
        *TOTAL_CHOLESTEROL_TABLE[:4],
        "." * 64,
        "* | < 170 | * | < 170 | * | DESEJÁVEL | *",
        *TOTAL_CHOLESTEROL_TABLE[6:],
    ]
    _, _, blocks = blocks_of(pages(body))

    assert len(blocks) == 1
    assert not any(line.startswith(("+---", "....")) for line in blocks[0].lines)
    assert "* | < 170 | * | < 170 | * | DESEJÁVEL | *" in blocks[0].lines


def test_header_footer_and_page_counters_never_reach_a_block():
    _, _, blocks = blocks_of(pages(ALBUMIN + ESTRADIOL, LH))

    for block in blocks:
        for line in block.lines:
            assert not line.startswith(("Pedido:", "Médico:", "Convênio:", "Softl@b", "Pag.:"))
            assert "CRM-PJ" not in line and "DIREÇÃO" not in line and "Licença" not in line
            assert "assinado digitalmente" not in line and "ICP-Brasil" not in line


def test_header_lines_come_from_the_repeated_top_of_the_pages():
    header, _, _ = blocks_of(pages(ALBUMIN, TSH))

    assert header[0] == f"Pedido: {ORDER} {PATIENT}"
    assert header[1].startswith("Médico: ") and ISSUED in header[1]


def test_issue_date_printed_at_the_bottom_of_the_page_survives_the_boilerplate_strip():
    page_list = pages(ALBUMIN, TSH, GLUCOSE, cadastro_on_top=False)

    header, preface, blocks = blocks_of(page_list)

    assert not any("cadastro" in line for line in header)
    assert preface == [f"Data cadastro: {ISSUED}"]
    assert not any("Data cadastro" in line for block in blocks for line in block.lines)


def test_single_page_report_keeps_its_header_out_of_the_blocks_and_in_the_preface():
    page = pages(COVID)[0]

    _, preface, blocks = blocks_of([page])

    assert preface == header_lines(1, 1)[:2]
    assert titles(blocks) == ["TESTE RÁPIDO PARA COVID-19"]


def test_the_original_material_layout_is_not_taken_for_softlab():
    material_layout = [
        PageText(
            1,
            [
                "GLICOSE | 84 mg/dL",
                "Material: soro",
                "Método: Hexoquinase",
                "Data da coleta: 09/05/2011 - 10:40:30",
                "Valores de Referência: | 70 a 99 mg/dL",
                "Liberado por: Dra. Fulana CRM-CE:515",
            ],
            "text",
        )
    ]

    _, blocks = split_blocks([material_layout[0].lines])

    assert [b.layout for b in blocks] == [""]
    assert blocks[0].title == "GLICOSE | 84 mg/dL"
