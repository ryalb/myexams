"""Parser da saída compacta do LLM: `n|nome|valor|unidade|calc|referência`."""

from apps.exams.extraction.blocks import Block
from apps.exams.extraction.compact import (
    MAX_ROWS,
    parse_compact,
    parse_compact_rows,
    parse_header_lines,
)

LINES = [
    "Hemácias | 4,85 milhões/uL | 4,50 a 6,50 milhões/uL",
    "Hemoglobina | g/dL | 13,50 a 18,00 g/dL",
    "Neutrófilos | 60,0 % 2.400 /uL | 40,0 a 75,0 | 2.000 a 7.000",
]


def block(lines=LINES) -> Block:
    return Block(title="", lines=list(lines), page=1)


def test_well_formed_lines_become_rows():
    text = "\n".join(
        [
            "1|Hemácias|4,85|milhões/uL|0|4,50 a 6,50 milhões/uL",
            "2|Hemoglobina||g/dL|0|13,50 a 18,00 g/dL",
            "3|Neutrófilos|60,0|%|0|40,0 a 75,0",
            "3|Neutrófilos (absoluto)|2.400|/uL|0|2.000 a 7.000",
        ]
    )
    rows = parse_compact_rows(text, block())
    assert [r["name"] for r in rows] == [
        "Hemácias",
        "Hemoglobina",
        "Neutrófilos",
        "Neutrófilos (absoluto)",
    ]
    assert rows[0] == {
        "name": "Hemácias",
        "value_text": "4,85",
        "unit": "milhões/uL",
        "reference_text": "4,50 a 6,50 milhões/uL",
        "printed_flag": "",
        "is_calculated": False,
        "source_line": LINES[0],
    }
    assert rows[1]["value_text"] == ""  # valor ausente fica vazio (nunca inventado)
    assert rows[2]["source_line"] == rows[3]["source_line"] == LINES[2]


def test_preamble_fences_and_blank_lines_are_ignored():
    text = (
        "Claro! Aqui estão os resultados:\n\n"
        "```\n"
        "1|Hemácias|4,85|milhões/uL|0|\n"
        "```\n"
        "Espero ter ajudado.\n"
    )
    parsed = parse_compact(text, block())
    assert [r["name"] for r in parsed.rows] == ["Hemácias"]
    assert parsed.skipped == 0  # texto solto não é "resultado inutilizável"


def test_lines_with_too_few_separators_are_skipped():
    text = "1|Hemácias|4,85|milhões/uL|0\n2|Hemoglobina|13|g/dL\nsó texto"
    assert parse_compact_rows(text, block()) == []


def test_exactly_five_separators_with_an_empty_reference_is_valid():
    (row,) = parse_compact_rows("2|Hemoglobina|13,5|g/dL|0|", block())
    assert row["reference_text"] == ""
    assert row["source_line"] == LINES[1]


def test_extra_pipes_stay_inside_the_reference_and_are_cleaned():
    (row,) = parse_compact_rows("3|Neutrófilos|60,0|%|0|40,0 a 75,0 | 2.000 a 7.000 |", block())
    assert row["reference_text"] == "40,0 a 75,0 2.000 a 7.000"


def test_reference_bands_separated_by_semicolon_or_literal_newline_become_lines():
    (a,) = parse_compact_rows("1|X|1|mg/dL|0|Homens: 1 a 2; Mulheres: 3 a 4", block())
    (b,) = parse_compact_rows("1|X|1|mg/dL|0|Homens: 1 a 2\\nMulheres: 3 a 4", block())
    assert a["reference_text"] == b["reference_text"] == "Homens: 1 a 2\nMulheres: 3 a 4"


def test_bad_line_number_falls_back_to_a_search_for_the_value():
    for number in ("99", "0", "-1", "abc", ""):
        (row,) = parse_compact_rows(f"{number}|Hemácias|4,85|milhões/uL|0|", block())
        assert row["source_line"] == LINES[0], number


def test_bad_line_number_with_a_value_that_is_nowhere_has_no_source_line():
    (row,) = parse_compact_rows("99|Hemácias|9,99|milhões/uL|0|", block())
    assert row["source_line"] == ""


def test_line_number_tolerates_brackets_and_dots():
    for number in ("[2]", "2.", "(2)", " 2 "):
        (row,) = parse_compact_rows(f"{number}|Hemoglobina||g/dL|0|", block())
        assert row["source_line"] == LINES[1], number


def test_leading_pipe_markdown_style_is_accepted():
    (row,) = parse_compact_rows("| 1 | Hemácias | 4,85 | milhões/uL | 0 | 4,50 a 6,50 |", block())
    assert (row["name"], row["value_text"]) == ("Hemácias", "4,85")


def test_calc_flag():
    rows = parse_compact_rows(
        "\n".join(f"1|X{i}|1|mg/dL|{flag}|" for i, flag in enumerate(["1", "0", "", "sim", "x"])),
        block(),
    )
    assert [r["is_calculated"] for r in rows] == [True, False, False, True, False]


def test_rows_without_a_name_and_the_format_header_are_skipped():
    text = "\n".join(
        [
            "n|nome|valor|unidade|calc|referência",
            "|---|---|---|---|---|---|",
            "1||4,85|mg/dL|0|",
            "1|Hemácias|4,85|milhões/uL|0|",
        ]
    )
    parsed = parse_compact(text, block())
    assert [r["name"] for r in parsed.rows] == ["Hemácias"]
    assert parsed.skipped == 2


def test_rows_are_capped():
    text = "\n".join(f"1|Exame{i}|{i}|mg/dL|0|" for i in range(MAX_ROWS + 15))
    parsed = parse_compact(text, block())
    assert len(parsed.rows) == MAX_ROWS
    assert parsed.skipped == 15


def test_empty_or_none_text():
    assert parse_compact_rows("", block()) == []
    assert parse_compact_rows(None, block()) == []  # type: ignore[arg-type]


# --- cabeçalho ----------------------------------------------------------------------------


def test_header_lines_map_to_the_header_fields():
    header = parse_header_lines(
        "\n".join(
            [
                "lab: Laboratório Exemplo",
                "accession: 021-11111-222",
                "patient: JOAO TESTE DA SILVA",
                "birth: 05/03/1977",
                "physician: DR EXEMPLO",
                "issued: 31/08/2026 - 13:57:40",
                "collected: 25/08/2026 - 07:01:51",
            ]
        )
    )
    assert header == {
        "laboratory_name": "Laboratório Exemplo",
        "accession_number": "021-11111-222",
        "patient_name": "JOAO TESTE DA SILVA",
        "patient_birth_date": "05/03/1977",
        "ordering_physician": "DR EXEMPLO",
        "issued_at": "31/08/2026 - 13:57:40",
        "collected_at": "25/08/2026 - 07:01:51",
    }


def test_header_parser_is_tolerant():
    header = parse_header_lines(
        "Aqui está:\n"
        "```\n"
        "- LAB: Laboratório Exemplo\n"
        "**Accession**: 123\n"
        "patient:\n"
        "birth: -\n"
        "unknown: x\n"
        "lab: Outro Lab\n"  # só a primeira ocorrência vale
        "issued: 31/08/2026 - 13:57:40\n"
        "```\n"
    )
    assert header["laboratory_name"] == "Laboratório Exemplo"
    assert header["accession_number"] == "123"
    assert header["patient_name"] == ""
    assert header["patient_birth_date"] == ""
    assert header["issued_at"] == "31/08/2026 - 13:57:40"
    assert "ordering_physician" not in header  # ausente: o regex de reserva completa


def test_header_value_keeps_colons_after_the_first():
    header = parse_header_lines("issued: 31/08/2026 - 13:57:40")
    assert header["issued_at"] == "31/08/2026 - 13:57:40"
