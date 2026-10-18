"""Variantes do layout com `Material:`/`Liberado por` (laudo antigo, `Numero.:` no cabeçalho).

Nesse laudo o `Método:` vem ANTES de `Material:` (o título é a linha anterior ao método), o
resultado é `NOME: 4,2mmol/L` (dois-pontos, unidade colada), a coleta vem na linha de fim e o
sinal de menos das faixas é U+2212. Também: nome de exame em duas linhas.
Dados inventados.
"""

from datetime import date
from decimal import Decimal

import pytest

from apps.exams import tasks
from apps.exams.extraction.blocks import split_blocks, strip_boilerplate
from apps.exams.extraction.fakes import FakeLLMClient
from apps.exams.extraction.layouts import extract_inline_block
from apps.exams.extraction.linearize import PageText, clean_lines
from apps.exams.extraction.pipeline import parse_header
from apps.exams.extraction.reference import applicable_bands, parse_reference
from apps.exams.models import ExamDocument, ResultFlag, ResultOrigin

HEADER = [
    "Numero.: 850−000123",
    "Entrada: 09/05/2011",
    "Emissao: 16/05/2011",
    "Cliente: JOAO TESTE DA SILVA",
    "Medico : FULANA DE TAL",
]
END = [
    "Liberado por: FULANO EXEMPLO | CRF | : 0000000 Data coleta: | 09/05/2011 07:41",
    "Liberado em: 09/05/2011 12:03 | Exame realizado no laboratorio:",
    "(cid:0)(cid:0)(cid:0)(cid:0)(cid:0)(cid:0)",
]
POTASSIUM = [
    "POTASSIO: 4,6mmol/L",
    "Método: Potenciometria Indireta",
    "Material: Soro",
    "Valor de Referência: 3,5 a 5,5 mMol/L",
    "Crianças ate 1 mês.: 3,6 a 6,1 mMol/L",
    "de 2 a 12 meses....: 3,6 a 5,8 mMol/L",
    *END,
]
URIC_ACID = [
    "ACIDO URICO:4 | mg/dL",
    "Método: Uricase / Peroxidase",
    "Material: Soro",
    "Valores de Referência:",
    "Homens....: 3,7 a 9,2 mg/dL",
    "Mulheres..: 3,1 a 7,8 mg/dL",
    *END,
]
TSH = [
    "TSH − ULTRA SENSIVEL: | 2,114 | uUI/mL",
    "Método: Quimioluminescência",
    "Material: Soro",
    "Valores de Referência",
    "0,500 a 5,000 uUI/mL",
    "Nota: A partir de 20/06/2010 houve alteração na metodologia.",
    *END,
]
T4 = [
    "T4 LIVRE: | 1,34ng/dL",
    "Método: Quimioluminescência",
    "Material: Soro",
    "Valores de Referência:",
    "0 a 1 mês........: 0,65 a 2,40 ng/dL",
    "1 a 7 meses......: 0,70 a 2,00 ng/dL",
    "1 a 12 anos......: 0,80 a 1,75 ng/dL",
    "Acima de 12 anos.: 0,70 a 1,80 ng/dL",
    *END,
]
HEMOGRAM = [
    "HEMOGRAMA COMPLETO",
    "Material: Sangue | Metodo: Automacao Roche",
    "ERITROGRAMA | Valores encontrados | Valores de Referencia",
    "Homem | Mulher",
    "Hemacias em milhoes/mm3....: | 5,3 | 4.5 − 6.1 | 4.1 − 5.3",
    "Hemoglobina em g/dL........: 15,60 | 12.8 − 17.8 11.5 − 16.4",
    "Hematocrito em % ..........: 46,3 | 40.0 − 54.0 36.0 − 48.0",
    *END,
]
CREATININE = [
    "CREATININA",
    "Método: Cinético Otimizado",
    "Material: Soro",
    "Valores de Referência:",
    "Adultos: 0,60 a 1,20 mg/dL",
    "Creatinina...................: | 0,94 mg/dL",
    *END,
]
SPLIT_NAME = [
    "GLOBULINA LIGADORA DE HORMÔNIOS | 24,8 nmol/L",
    "SEXUAIS",
    "Material: soro",
    "Método: Imunoensaio",
    "Data da coleta: 03/04/2016 - 09:15:00",
    "Valores de Referência: | Homens: | 13,5 a 71,4 nmol/L",
    "Liberado por: Dra. Fulana CRM-CE:1",
]


def pages_of(*bodies: list[str]) -> list[PageText]:
    return [PageText(n, [*HEADER, *body], "text") for n, body in enumerate(bodies, start=1)]


def blocks_of(*bodies: list[str]):
    cleaned, header = strip_boilerplate(pages_of(*bodies))
    _, blocks = split_blocks(cleaned)
    return header, blocks


def test_title_is_the_line_before_the_method_when_the_method_precedes_the_material():
    _, blocks = blocks_of(POTASSIUM + URIC_ACID, TSH)

    assert [b.title for b in blocks] == [
        "POTASSIO: 4,6mmol/L",
        "ACIDO URICO:4 | mg/dL",
        "TSH − ULTRA SENSIVEL: | 2,114 | uUI/mL",
    ]
    assert blocks[0].specimen == "Soro"
    assert blocks[0].method == "Potenciometria Indireta"


def test_collection_date_comes_from_the_release_line_when_the_block_has_none():
    _, blocks = blocks_of(POTASSIUM)

    assert blocks[0].collected_at == "09/05/2011 07:41"


def test_specimen_is_the_first_column_and_the_method_may_share_its_line():
    _, blocks = blocks_of(HEMOGRAM)

    assert blocks[0].specimen == "Sangue"
    assert blocks[0].method == "Automacao Roche"


def test_labelled_result_with_the_unit_glued_or_in_its_own_column():
    rows = {}
    for body in (POTASSIUM, URIC_ACID, TSH, T4):
        _, blocks = blocks_of(body)
        for row in extract_inline_block(blocks[0]) or []:
            rows[row["name"]] = row

    assert (rows["POTASSIO"]["value_text"], rows["POTASSIO"]["unit"]) == ("4,6", "mmol/L")
    assert (rows["ACIDO URICO"]["value_text"], rows["ACIDO URICO"]["unit"]) == ("4", "mg/dL")
    tsh = (
        rows["TSH - ULTRA SENSIVEL"]
        if "TSH - ULTRA SENSIVEL" in rows
        else rows["TSH − ULTRA SENSIVEL"]
    )
    assert (tsh["value_text"], tsh["unit"]) == ("2,114", "uUI/mL")
    assert rows["T4 LIVRE"]["unit"] == "ng/dL"


def test_reference_header_without_a_colon_at_the_end_of_the_line_starts_the_reference():
    _, blocks = blocks_of(TSH)

    row = extract_inline_block(blocks[0])[0]

    assert row["reference_text"] == "0,500 a 5,000 uUI/mL"


def test_blocks_with_columns_in_the_middle_of_the_result_go_to_the_llm():
    _, blocks = blocks_of(HEMOGRAM)
    assert extract_inline_block(blocks[0]) is None

    _, blocks = blocks_of(CREATININE)
    assert extract_inline_block(blocks[0]) is None


def test_name_continued_in_the_next_line_belongs_to_the_same_block_and_name():
    lines = [
        ["Cliente: JOAO TESTE DA SILVA"],
        [
            "TESTOSTERONA TOTAL | 612,40 ng/dL",
            "Material: soro",
            "Liberado por: Dra. Fulana CRM-CE:1",
        ],
        SPLIT_NAME,
    ]
    flat = [line for chunk in lines[1:] for line in chunk]

    _, blocks = split_blocks([flat])

    assert [b.title for b in blocks] == [
        "TESTOSTERONA TOTAL | 612,40 ng/dL",
        "GLOBULINA LIGADORA DE HORMÔNIOS | 24,8 nmol/L",
    ]
    row = extract_inline_block(blocks[1])[0]
    assert row["name"] == "GLOBULINA LIGADORA DE HORMÔNIOS SEXUAIS"
    assert (row["value_text"], row["unit"]) == ("24,8", "nmol/L")
    assert row["reference_text"] == "Homens: 13,5 a 71,4 nmol/L"


def test_unicode_minus_becomes_a_hyphen_and_unmapped_glyph_lines_are_dropped():
    lines = clean_lines(
        ["Numero.: 850−000123", "Hemacias: 4.5 − 6.1", "(cid:0)(cid:0)(cid:0)", "ok"]
    )

    assert lines == ["Numero.: 850-000123", "Hemacias: 4.5 - 6.1", "ok"]


def test_header_with_numero_and_emissao_is_read_by_regex():
    header = parse_header({}, "\n".join(clean_lines(HEADER)))

    assert header.accession_number == "850-000123"
    assert header.patient_name == "JOAO TESTE DA SILVA"
    assert header.ordering_physician == "FULANA DE TAL"
    assert header.issued_at.date() == date(2011, 5, 16)


def test_age_ranges_in_months_never_become_the_result_range():
    # "0 a 1 mês" virava a faixa 0-1 (e o resultado, "alto")
    bands = parse_reference("\n".join(T4[4:8]))
    selected, _ = applicable_bands(bands, "male", 35)

    adult = [b for b in selected if b.low == Decimal("0.70") and b.high == Decimal("1.80")]
    assert adult
    assert not any(b.high == Decimal("1") for b in bands)
    assert not any(b.low == Decimal("0.65") for b in selected)


@pytest.mark.django_db
def test_end_to_end_without_the_llm(user, monkeypatch):
    client = FakeLLMClient()
    monkeypatch.setattr(tasks, "build_llm_client", lambda: client)
    page_list = pages_of(POTASSIUM + URIC_ACID + T4, TSH)
    document = ExamDocument.objects.create(
        user=user,
        sha256="d" * 64,
        original_filename="laudo.pdf",
        page_count=2,
        page_text=[
            {"number": p.number, "method": "text", "lines": clean_lines(p.lines)} for p in page_list
        ],
    )

    tasks.process_exam_document(document.pk, True)
    document.refresh_from_db()

    assert client.header_calls == [] and client.result_calls == []
    assert document.accession_number == "850-000123"
    assert document.collected_on == date(2011, 5, 9)
    current = document.results.filter(origin=ResultOrigin.CURRENT)
    assert current.count() == 4
    potassium = current.get(name_raw="POTASSIO")
    assert (potassium.value_numeric, potassium.unit, potassium.flag) == (
        Decimal("4.6"),
        "mmol/L",
        ResultFlag.NORMAL,
    )
    t4 = current.get(name_raw="T4 LIVRE")
    assert (t4.ref_low, t4.ref_high, t4.flag) == (
        Decimal("0.70"),
        Decimal("1.80"),
        ResultFlag.NORMAL,
    )
    assert current.get(name_raw="ACIDO URICO").ref_high == Decimal("9.2")
