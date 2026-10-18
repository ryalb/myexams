"""Segmentação do layout "ficha": um exame por trecho, sem âncora `Material:`."""

from datetime import date

from apps.exams.extraction.blocks import split_blocks, strip_boilerplate
from apps.exams.extraction.linearize import PageText

from .ficha_fixtures import (
    CHOLESTEROL,
    HDL,
    HEMOGRAM,
    HISTORY_PAGE_BODY,
    PATIENT,
    UREA,
    header_lines,
    page,
    pages,
    received,
    signature,
)


def blocks_of(page_list: list[PageText]):
    cleaned, header = strip_boilerplate(page_list)
    preface, blocks = split_blocks(cleaned)
    return header, preface, blocks


def test_boilerplate_strips_the_three_header_and_the_footer_lines_but_keeps_exam_lines():
    page_list = pages(CHOLESTEROL + UREA, HDL)

    cleaned, header = strip_boilerplate(page_list)

    assert header == header_lines()
    for lines in cleaned:
        assert not any(
            line.startswith(("Cliente:", "Médico:", "CRBM", "A interpretação")) for line in lines
        )
        assert not any("Impresso em" in line for line in lines)
    assert cleaned[0][0] == "COLESTEROL TOTAL, soro"
    assert cleaned[1][0] == "HDL-COLESTEROL, soro"
    assert any(line.startswith("Assinatura digital") for line in cleaned[1])


def test_one_block_per_exam_with_title_method_collection_date_and_specimen():
    _, _, blocks = blocks_of(pages(CHOLESTEROL + UREA, HDL))

    assert [b.title for b in blocks] == [
        "COLESTEROL TOTAL, soro",
        "UREIA, soro",
        "HDL-COLESTEROL, soro",
    ]
    cholesterol, urea, _ = blocks
    assert {b.layout for b in blocks} == {"ficha"}
    assert cholesterol.method == "Ensaio enzimático"
    assert urea.method == "Cinético, UV"
    assert cholesterol.collected_at == "10/02/2025"
    assert cholesterol.specimen == "soro"
    assert [b.page for b in blocks] == [1, 1, 2]


def test_block_keeps_reference_lines_and_stops_at_the_signature():
    _, _, blocks = blocks_of(pages(CHOLESTEROL + UREA, HDL))

    lines = blocks[0].lines
    assert "Com ou sem jejum de 12 horas:" in lines
    assert "Desejável: menor que 190 mg/dL." in lines
    assert any(line.startswith("RECEBIDO/COLETADO EM") for line in lines)
    assert not any(line.startswith("Assinatura digital") for line in lines)
    # O exame seguinte não vaza para o anterior.
    assert not any("UREIA" in line for line in lines)
    assert blocks[1].lines[0] == "UREIA, soro"


def test_table_exam_with_several_result_headers_is_a_single_block():
    _, _, blocks = blocks_of(pages(HEMOGRAM, CHOLESTEROL))

    assert [b.title for b in blocks] == ["HEMOGRAMA, sangue total", "COLESTEROL TOTAL, soro"]
    hemogram = blocks[0]
    assert hemogram.specimen == "sangue total"
    assert any("PLAQUETAS" in line for line in hemogram.lines)
    assert any(line.startswith("NOTA(1)") for line in hemogram.lines)


def test_history_pages_never_become_blocks_or_lines():
    page_list = pages(CHOLESTEROL, HISTORY_PAGE_BODY, HISTORY_PAGE_BODY)

    _, _, blocks = blocks_of(page_list)

    assert [b.title for b in blocks] == ["COLESTEROL TOTAL, soro"]
    assert not any("EVOLUTIVO" in line or "ANALITOS" in line for b in blocks for line in b.lines)


def test_history_after_the_last_exam_on_the_same_page_is_dropped():
    _, _, blocks = blocks_of(pages(CHOLESTEROL + HISTORY_PAGE_BODY, UREA))

    assert [b.title for b in blocks] == ["COLESTEROL TOTAL, soro", "UREIA, soro"]
    assert not any("ANALITOS" in line for b in blocks for line in b.lines)


def test_single_page_document_keeps_header_in_the_preface_and_drops_the_footer():
    _, preface, blocks = blocks_of(pages(CHOLESTEROL))

    assert [b.title for b in blocks] == ["COLESTEROL TOTAL, soro"]
    assert any(PATIENT in line for line in preface)
    assert not any("Impresso em" in line or "CRBM" in line for line in blocks[0].lines)
    assert not any(line.startswith("Cliente:") for line in blocks[0].lines)


def test_unstripped_header_and_footer_between_exams_do_not_pollute_blocks():
    # Duas páginas diferentes demais para o cabeçalho ser detectado como repetido: as linhas
    # de cabeçalho/rodapé continuam no fluxo e não podem virar título nem corpo de bloco.
    first = page(1, 2, CHOLESTEROL)
    second = PageText(
        2,
        ["Cliente: | OUTRO NOME", "Data de Nascimento: 01/01/1990 | Ficha: | 1", *UREA],
        "text",
    )
    flat_pages = [first.lines, second.lines]

    _, blocks = split_blocks(flat_pages)

    assert [b.title for b in blocks] == ["COLESTEROL TOTAL, soro", "UREIA, soro"]
    assert not any("OUTRO NOME" in line for b in blocks for line in b.lines)


def test_continuation_marker_inside_a_table_is_dropped():
    first = HEMOGRAM[:-6]
    second = ["CONTINUAÇÃO (HEMOGRAMA, sangue total)", *HEMOGRAM[-6:]]

    _, _, blocks = blocks_of(pages(first, second, UREA))

    assert [b.title for b in blocks] == ["HEMOGRAMA, sangue total", "UREIA, soro"]
    assert not any(line.startswith("CONTINUAÇÃO") for line in blocks[0].lines)
    assert any("PLAQUETAS" in line for line in blocks[0].lines)


def test_block_without_signature_still_ends_at_the_received_line():
    no_signature = [line for line in CHOLESTEROL if not line.startswith("Assinatura")]

    _, _, blocks = blocks_of(pages(no_signature + UREA))

    assert [b.title for b in blocks] == ["COLESTEROL TOTAL, soro", "UREIA, soro"]


def test_previous_results_table_is_parsed_into_block_history():
    body = [
        "HEMOGLOBINA GLICADA (A1C), sangue total",
        "Método: Imunoensaio",
        "RESULTADO",
        "6,0 %",
        "VALORES DE REFERÊNCIA",
        "Indivíduos sem diabetes mellitus:",
        "RESULTADOS ANTERIORES:",
        "FICHA | DATA | RESULTADO",
        "========== | ========== | =========",
        "9990000002 | 19/08/2023 | 5,9",
        "9990000003 | 03/04/2022 | 6,1",
        *received(),
    ]

    _, _, blocks = blocks_of(pages(body))

    assert blocks[0].previous == [(date(2023, 8, 19), "5,9"), (date(2022, 4, 3), "6,1")]


def test_documents_without_result_headers_use_the_page_fallback():
    first = ["RM COLUNA LOMBAR", "TÉCNICA", "Sagital TSE ponderado", "ANÁLISE", "Eixo preservado."]
    second = ["RM COLUNA TORÁCICA", "TÉCNICA", "Axial TSE ponderado", "ANÁLISE", "Canal normal."]

    _, _, blocks = blocks_of(pages([*first, signature()], [*second, signature()]))

    assert {b.layout for b in blocks} == {""}
    assert all(b.title == "" for b in blocks)
