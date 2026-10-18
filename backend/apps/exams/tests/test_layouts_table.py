"""Regras de tabela (`extract_table_rows`), orçamento de tempo do LLM e leitura do cabeçalho.

Dados sintéticos (nunca laudos reais). O Ollama nunca é chamado: o LLM é um fake em memória.
A primeira metade testa a camada de regras com blocos montados à mão; a segunda, o pipeline
de ponta a ponta com PDFs gerados.
"""

from decimal import Decimal

import pytest

from apps.exams import tasks
from apps.exams.codes import WarningCode
from apps.exams.extraction import pipeline
from apps.exams.extraction.blocks import Block
from apps.exams.extraction.fakes import FakeLLMClient
from apps.exams.extraction.layouts import extract_table_rows
from apps.exams.models import DocumentStatus, ResultOrigin

from .conftest import (
    SAMPLE_HEADER,
    SPARSE_HEADER,
    build_pdf,
    create_document,
    qualitative_block,
    qualitative_llm_results,
    row,
)


def block(*lines: str) -> Block:
    return Block(title=lines[0], lines=list(lines), page=1)


def table(*rows: str, title: str = "PERFIL BIOQUÍMICO") -> Block:
    return block(title, "Material: soro", "Método: Automatizado", *rows)


def slim(rows: list[dict]) -> list[tuple[str, str, str, str, str]]:
    """(nome, valor, unidade, referência, unit_hint): o que importa em cada linha."""
    return [
        (r["name"], r["value_text"], r["unit"], r["reference_text"], r.get("unit_hint", ""))
        for r in rows
    ]


GLUCOSE = "Glicose | 90 | mg/dL | 70 a 99 mg/dL"
UREA = "Urea | 30 | mg/dL | 10 a 50 mg/dL"


# --- gramática A: NOME | VALOR | UNIDADE | REFERÊNCIA ------------------------------------------


def test_grammar_a_value_unit_and_reference_in_separate_cells():
    rows = extract_table_rows(table(GLUCOSE, UREA))

    assert slim(rows) == [
        ("Glicose", "90", "mg/dL", "70 a 99 mg/dL", ""),
        ("Urea", "30", "mg/dL", "10 a 50 mg/dL", ""),
    ]
    assert [r["source_line"] for r in rows] == [GLUCOSE, UREA]
    assert all(r["printed_flag"] == "" and r["is_calculated"] is False for r in rows)


def test_grammar_a_without_reference_keeps_an_empty_reference():
    rows = extract_table_rows(table(GLUCOSE, "Urea | 30 | mg/dL"))
    assert slim(rows)[1] == ("Urea", "30", "mg/dL", "", "")


def test_calculated_names_are_flagged_as_calculated():
    rows = extract_table_rows(table(GLUCOSE, "LDL calculado | 90 | mg/dL | < 130 mg/dL"))
    assert [r["is_calculated"] for r in rows] == [False, True]


# --- gramática B: NOME | VALOR UNIDADE | REFERÊNCIA --------------------------------------------


def test_grammar_b_value_and_unit_in_the_same_cell():
    rows = extract_table_rows(
        table("Glicose | 90 mg/dL | 70 a 99 mg/dL", "Hemácias | 4,85 milhões/uL | 4,50 a 6,50")
    )
    assert slim(rows) == [
        ("Glicose", "90", "mg/dL", "70 a 99 mg/dL", ""),
        ("Hemácias", "4,85", "milhões/uL", "4,50 a 6,50", ""),
    ]


def test_grammar_b_without_reference():
    rows = extract_table_rows(table("Glicose | 90 mg/dL", "Hematócrito | 42,0 %"))
    assert slim(rows) == [
        ("Glicose", "90", "mg/dL", "", ""),
        ("Hematócrito", "42,0", "%", "", ""),
    ]


# --- gramática C: duas colunas de resultado => duas linhas -------------------------------------


def hemogram(*rows: str) -> Block:
    return block("HEMOGRAMA", "Material: sangue", "LEUCOGRAMA | Valores de referência", *rows)


def test_grammar_c_two_result_columns_make_two_rows_with_paired_references():
    line = "Neutrófilos | 60,0 % | 2.400 /uL | 40,0 a 75,0 % | 2.000 a 7.000 /uL"
    rows = extract_table_rows(hemogram(line))

    assert slim(rows) == [
        ("Neutrófilos", "60,0", "%", "40,0 a 75,0 %", ""),
        ("Neutrófilos (absoluto)", "2.400", "/uL", "2.000 a 7.000 /uL", ""),
    ]
    assert [r["source_line"] for r in rows] == [line, line]


def test_grammar_c_without_printed_units_carries_unit_hints():
    rows = extract_table_rows(
        hemogram("Neutrófilos | 60,0 | 2.400 | 40,0 a 75,0 | 2.000 a 7.000"),
    )
    assert slim(rows) == [
        ("Neutrófilos", "60,0", "", "40,0 a 75,0", "%"),
        ("Neutrófilos (absoluto)", "2.400", "", "2.000 a 7.000", "/µL"),
    ]


def test_grammar_c_with_both_values_in_one_cell():
    rows = extract_table_rows(
        hemogram("Neutrófilos | 60,0 2.400 | 40,0 a 75,0 | 2.000 a 7.000"),
    )
    assert slim(rows) == [
        ("Neutrófilos", "60,0", "", "40,0 a 75,0", "%"),
        ("Neutrófilos (absoluto)", "2.400", "", "2.000 a 7.000", "/µL"),
    ]


def test_grammar_c_column_units_line_replaces_the_hints():
    rows = extract_table_rows(
        hemogram("% | /µL", "Neutrófilos | 60,0 | 2.400 | 40,0 a 75,0 | 2.000 a 7.000"),
    )
    assert slim(rows) == [
        ("Neutrófilos", "60,0", "%", "40,0 a 75,0", ""),
        ("Neutrófilos (absoluto)", "2.400", "/µL", "2.000 a 7.000", ""),
    ]


@pytest.mark.parametrize(
    ("reference", "percent_ref", "absolute_ref"),
    [
        ("2.000 a 7.000", "", "2.000 a 7.000"),  # limite acima de 100: só cabe em contagem
        ("2.000 a 7.000 /uL", "", "2.000 a 7.000 /uL"),  # unidade impressa manda
        ("40,0 a 75,0", "40,0 a 75,0", ""),  # casa decimal: percentual
        ("0,0 a 5,0", "0,0 a 5,0", ""),
        ("40,0 a 75,0 %", "40,0 a 75,0 %", ""),  # unidade impressa manda
    ],
)
def test_grammar_c_single_reference_goes_to_the_column_it_belongs_to(
    reference, percent_ref, absolute_ref
):
    rows = extract_table_rows(hemogram(f"Neutrófilos | 60,0 | 2.400 | {reference}"))
    assert [r["reference_text"] for r in rows] == [percent_ref, absolute_ref]


@pytest.mark.parametrize("reference", ["< 100", "0 a 80"])
def test_grammar_c_single_reference_with_undeterminable_column_goes_to_the_llm(reference):
    # Sem unidade, sem casa decimal e com limites <= 100 a referência pode ser do percentual ou
    # de uma contagem pequena: o bloco inteiro vai ao LLM em vez de chutar a coluna.
    assert extract_table_rows(hemogram(f"Basófilos | 0,5 | 40 | {reference}")) is None


def test_block_title_that_is_itself_a_result_goes_to_the_llm():
    # "PERFIL | 5 g/dL" seria emitido como um resultado falso ao lado das linhas da tabela.
    lines = [
        "PERFIL | 5 g/dL",
        "Material: soro",
        "Glicose | 90 | mg/dL | 70 a 99 mg/dL",
        "Ureia | 30 | mg/dL | 10 a 50 mg/dL",
    ]
    assert extract_table_rows(block(*lines)) is None


def test_block_title_without_value_is_still_just_a_title():
    lines = [
        "PERFIL BIOQUÍMICO",
        "Material: soro",
        "Glicose | 90 | mg/dL | 70 a 99 mg/dL",
        "Ureia | 30 | mg/dL | 10 a 50 mg/dL",
    ]
    rows = extract_table_rows(block(*lines))
    assert [r["name"] for r in rows] == ["Glicose", "Ureia"]


# --- separadores de milhar, seções, metadados ---------------------------------------------------


def test_thousands_separators_are_kept_as_printed():
    rows = extract_table_rows(
        block(
            "HEMOGRAMA",
            "Material: sangue",
            "ERITROGRAMA | Valores de referência",
            "Hemácias | 4,85 | milhões/uL | 4,50 a 6,50 milhões/uL",
            "Plaquetas | 250.000 | /uL | 150.000 a 450.000 /uL",
        )
    )
    assert slim(rows) == [
        ("Hemácias", "4,85", "milhões/uL", "4,50 a 6,50 milhões/uL", ""),
        ("Plaquetas", "250.000", "/uL", "150.000 a 450.000 /uL", ""),
    ]


def test_section_observation_and_meta_lines_are_skipped():
    lines = [
        "HEMOGRAMA",
        "Material: sangue",
        "Método: Automatizado",
        "Data da coleta: 25/08/2026 - 07:01:57",
        "ERITROGRAMA | Valores de referência",
        "Hemácias | 4,85 | milhões/uL | 4,50 a 6,50 milhões/uL",
        "Observação: | Hemácias normocíticas",
        "LEUCOGRAMA",
        "Leucócitos | 4.000 | /uL | 4.000 a 10.000 /uL",
        "Anteriores: | 25/04/2022 - 4.100",
        "Liberado por: Dr. Teste CRM-CE 1",
    ]
    rows = extract_table_rows(block(*lines))
    assert [r["name"] for r in rows] == ["Hemácias", "Leucócitos"]


def test_block_title_without_a_value_is_skipped():
    rows = extract_table_rows(table(GLUCOSE, title="PERFIL | Bioquímica"))
    assert [r["name"] for r in rows] == ["Glicose"]


def test_comparator_values_are_kept_with_the_comparator():
    rows = extract_table_rows(
        table("PCR | < 0,5 | mg/dL | < 1,0 mg/dL", "Ferritina | > 1000 | ng/mL | 30 a 400 ng/mL")
    )
    assert slim(rows) == [
        ("PCR", "< 0,5", "mg/dL", "< 1,0 mg/dL", ""),
        ("Ferritina", "> 1000", "ng/mL", "30 a 400 ng/mL", ""),
    ]


def test_up_to_six_cells_are_accepted():
    line = "Glicose | 90 | mg/dL | 70 a 99 mg/dL | 80 a 100 mg/dL | 60 a 120 mg/dL"
    (result,) = extract_table_rows(table(line))
    assert result["reference_text"] == "70 a 99 mg/dL 80 a 100 mg/dL 60 a 120 mg/dL"


# --- dúvida => None para o bloco inteiro ---------------------------------------------------------


def test_one_unparsable_row_rejects_the_whole_block_never_half_a_table():
    good = table(GLUCOSE, UREA)
    assert extract_table_rows(good) is not None

    # A segunda linha tem uma referência em texto livre: nenhuma linha é emitida.
    assert extract_table_rows(table(GLUCOSE, "Urea | 30 | mg/dL | conforme a idade", UREA)) is None


@pytest.mark.parametrize(
    "bad_line",
    [
        "Aspecto | turvo | mg/dL | 70 a 99 mg/dL",  # valor não numérico nem qualitativo
        "Nitrito | Negativo | Negativo",  # célula qualitativa
        "Nitrito | Negativo | | Negativo",
        "Glicose | 90 | mg/dL | 70 a 99 mg/dL | 1 | 2 | 3",  # mais de 6 células
        "Glicose | 90 | blorp | 70 a 99 mg/dL",  # unidade não reconhecida (célula própria)
        "Glicose | 90 blorp | 70 a 99 mg/dL",  # unidade não reconhecida (junto do valor)
        "Glicose | 90 | 70 a 99 mg/dL",  # sem unidade em lugar nenhum
        "Glicose 90 mg/dL",  # resultado fora do formato de colunas
        "Glicose em jejum | 90 | mg/dL | 70 a 99 mg/dL",  # rótulo com palavra de faixa
    ],
)
def test_a_doubtful_row_sends_the_block_to_the_llm(bad_line):
    assert extract_table_rows(table(GLUCOSE, bad_line)) is None


def test_percent_above_100_in_grammar_c_is_rejected():
    ok = hemogram("Neutrófilos | 100 | 50 | 0 a 100 | 0 a 200")
    assert extract_table_rows(ok) is not None
    assert (
        extract_table_rows(hemogram("Neutrófilos | 160,0 | 2.400 | 40,0 a 75,0 | 2.000 a 7.000"))
        is None
    )


def test_grammar_c_requires_percent_and_count_units():
    assert (
        extract_table_rows(
            hemogram("Neutrófilos | 60,0 g/dL | 2.400 /uL | 40,0 a 75,0 | 2.000 a 7.000")
        )
        is None
    )
    assert (
        extract_table_rows(
            hemogram("Neutrófilos | 60,0 % | 2.400 mg/dL | 40,0 a 75,0 | 2.000 a 7.000")
        )
        is None
    )


@pytest.mark.parametrize(
    "lines",
    [
        ["PERFIL", GLUCOSE, UREA],  # sem a âncora `Material:`
        [
            "PERFIL",
            "Material: soro",
            GLUCOSE,
            "Valor de Referência: 70 a 99 mg/dL",
        ],  # layout de linha única
        ["PERFIL", "Material: soro"],  # nada a ler
    ],
)
def test_blocks_without_anchor_or_with_a_reference_section_are_not_tables(lines):
    assert extract_table_rows(block(*lines)) is None


# --- pipeline: tabela limpa sem LLM -------------------------------------------------------------

TABLE_BODY = [
    [(40, "PERFIL BIOQUÍMICO")],
    [(40, "Material: soro")],
    [(40, "Data da coleta: 25/08/2026 - 07:01:51")],
    row("ERITROGRAMA", "", "", "Valores de referência"),
    row("Glicose", "90", "mg/dL", "70 a 99 mg/dL"),
    row("Hemoglobina", "14,1 g/dL", "13,50 a 18,00 g/dL"),
    row("Neutrófilos", "60,0", "2.400", "40,0 a 75,0 | 2.000 a 7.000"),
    [(40, "Liberado por: Dr. Teste CRM-CE 1")],
]


class ForbiddenLLM(FakeLLMClient):
    """Registra e recusa qualquer chamada de extração: prova que o LLM não foi usado."""

    def __init__(self) -> None:
        super().__init__(header=SAMPLE_HEADER)
        self.attempts: list[str] = []

    def extract_header(self, text: str) -> dict:
        self.attempts.append("header")
        raise AssertionError("extract_header não deveria ser chamado")

    def extract_results(self, block_text: str, page: int) -> dict:
        self.attempts.append("results")
        raise AssertionError("extract_results não deveria ser chamado")


def process(monkeypatch, user, pdf, llm):
    monkeypatch.setattr(tasks, "build_llm_client", lambda: llm)
    document = create_document(user, pdf)
    tasks.process_exam_document(document.pk)
    document.refresh_from_db()
    return document, document.runs.get().stats


@pytest.mark.django_db
def test_clean_table_runs_end_to_end_without_any_llm_call(monkeypatch, user, tmp_path):
    pdf = build_pdf(tmp_path / "tabela.pdf", [TABLE_BODY])
    llm = ForbiddenLLM()

    document, stats = process(monkeypatch, user, pdf, llm)

    assert llm.attempts == []
    assert document.status == DocumentStatus.COMPLETED
    assert (stats["blocks"], stats["rule_blocks"], stats["table_blocks"]) == (1, 1, 1)
    assert stats["llm_calls"] == 0
    assert stats["llm_skipped_units"] == 0
    assert stats["header_by_llm"] is False
    assert stats["results"] == 4

    by_name = {
        r.name_raw: r for r in document.results.filter(origin=ResultOrigin.CURRENT).order_by("pk")
    }
    assert list(by_name) == ["Glicose", "Hemoglobina", "Neutrófilos", "Neutrófilos (absoluto)"]

    glucose = by_name["Glicose"]
    assert (glucose.value_numeric, glucose.unit, glucose.reference_raw) == (
        Decimal("90"),
        "mg/dL",
        "70 a 99 mg/dL",
    )
    assert glucose.analyte.slug == "glicose"
    assert glucose.section_raw == "ERITROGRAMA"
    assert glucose.method == ""  # o bloco não imprimiu `Método:`
    assert glucose.specimen == "soro"

    hemoglobin = by_name["Hemoglobina"]
    assert (hemoglobin.value_numeric, hemoglobin.unit) == (Decimal("14.1"), "g/dL")

    # Duas colunas sem unidade impressa: a unidade vem da dica, por código.
    percent, absolute = by_name["Neutrófilos"], by_name["Neutrófilos (absoluto)"]
    assert (percent.value_numeric, percent.unit, percent.reference_raw) == (
        Decimal("60.0"),
        "%",
        "40,0 a 75,0",
    )
    assert (absolute.value_numeric, absolute.unit, absolute.reference_raw) == (
        Decimal("2400"),
        "/µL",
        "2.000 a 7.000",
    )
    assert percent.analyte.slug != absolute.analyte.slug


# --- pipeline: orçamento de tempo do LLM --------------------------------------------------------

BUDGET = 10


class FakeClock:
    """Relógio manual: `pipeline._clock` devolve `now`; o fake do LLM o avança."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock(monkeypatch, settings):
    settings.EXTRACTION_LLM_BUDGET_SECONDS = BUDGET
    fake = FakeClock()
    monkeypatch.setattr(pipeline, "_clock", fake)
    return fake


def budget_pdf(tmp_path):
    """Página 1: NITRITO (LLM) + tabela (regras). Páginas 2 e 3: um bloco qualitativo cada."""
    page_1 = [*qualitative_block("NITRITO", "Negativo"), *TABLE_BODY]
    page_2 = qualitative_block("UROBILINOGÊNIO", "Normal")
    page_3 = qualitative_block("BILIRRUBINA", "Negativo")
    return build_pdf(tmp_path / "orcamento.pdf", [page_1, page_2, page_3])


def slow_llm(clock: FakeClock, seconds: float) -> FakeLLMClient:
    """Cada chamada de extração 'demora' `seconds` no relógio simulado."""

    def results(text: str) -> dict:
        clock.now += seconds
        return qualitative_llm_results(text)

    return FakeLLMClient(header=SAMPLE_HEADER, results=results)


@pytest.mark.django_db
def test_budget_exhausted_by_the_first_llm_unit_skips_the_rest(monkeypatch, user, tmp_path, clock):
    llm = slow_llm(clock, BUDGET + 1)

    document, stats = process(monkeypatch, user, budget_pdf(tmp_path), llm)

    # Só a primeira unidade de LLM (NITRITO, página 1) foi perguntada.
    assert len(llm.result_calls) == 1
    assert llm.result_calls[0].startswith("NITRITO")
    assert (stats["blocks"], stats["rule_blocks"], stats["table_blocks"]) == (4, 1, 1)
    assert stats["llm_calls"] == 1
    assert stats["llm_skipped_units"] == 2
    assert stats["skipped_blocks"] == 2
    assert stats["llm_seconds"] == BUDGET + 1

    # Avisos do laudo: um por bloco pulado (página + nome do exame) e um único do orçamento.
    assert document.warnings == [
        {"code": WarningCode.BLOCK_UNREADABLE, "params": {"page": 2, "title": "UROBILINOGÊNIO"}},
        {"code": WarningCode.BLOCK_UNREADABLE, "params": {"page": 3, "title": "BILIRRUBINA"}},
        {"code": WarningCode.LLM_BUDGET_EXCEEDED, "params": {"seconds": BUDGET}},
    ]
    assert document.status == DocumentStatus.NEEDS_REVIEW

    names = set(
        document.results.filter(origin=ResultOrigin.CURRENT).values_list("name_raw", flat=True)
    )
    assert {"NITRITO", "Glicose", "Hemoglobina", "Neutrófilos"} <= names  # tabela e 1º LLM salvos
    assert not names & {"UROBILINOGÊNIO", "BILIRRUBINA"}


@pytest.mark.django_db
def test_budget_not_reached_reads_every_llm_unit_without_warnings(
    monkeypatch, user, tmp_path, clock
):
    llm = slow_llm(clock, 1)

    document, stats = process(monkeypatch, user, budget_pdf(tmp_path), llm)

    assert len(llm.result_calls) == 3
    assert stats["llm_skipped_units"] == 0
    assert stats["llm_seconds"] == 3
    assert WarningCode.LLM_BUDGET_EXCEEDED not in {w["code"] for w in document.warnings}
    assert WarningCode.BLOCK_UNREADABLE not in {w["code"] for w in document.warnings}
    # Sem aviso do laudo (o status pode pedir revisão por causa de exames sem catálogo).
    assert document.warnings == []
    names = set(
        document.results.filter(origin=ResultOrigin.CURRENT).values_list("name_raw", flat=True)
    )
    assert {"NITRITO", "UROBILINOGÊNIO", "BILIRRUBINA", "Glicose"} <= names


@pytest.mark.django_db
def test_budget_of_zero_still_reads_rule_blocks_and_never_asks_the_llm(
    monkeypatch, user, tmp_path, settings
):
    settings.EXTRACTION_LLM_BUDGET_SECONDS = 0
    llm = ForbiddenLLM()
    pdf = build_pdf(
        tmp_path / "zero.pdf", [TABLE_BODY, qualitative_block("NITRITO", "Negativo")], SPARSE_HEADER
    )

    document, stats = process(monkeypatch, user, pdf, llm)

    assert llm.attempts == []  # nem os blocos, nem o cabeçalho esparso
    assert stats["llm_calls"] == 0
    assert stats["header_by_llm"] is False  # o teto já estourado: o modelo nem foi tentado
    assert stats["llm_skipped_units"] == 1
    assert {"code": WarningCode.LLM_BUDGET_EXCEEDED, "params": {"seconds": 0}} in document.warnings
    assert document.results.filter(name_raw="Glicose").exists()
    assert document.status == DocumentStatus.NEEDS_REVIEW


# --- pipeline: cabeçalho por regex primeiro -----------------------------------------------------


@pytest.mark.django_db
def test_header_found_by_regex_never_calls_the_llm_header(monkeypatch, user, tmp_path):
    pdf = build_pdf(tmp_path / "completo.pdf", [TABLE_BODY])  # cabeçalho padrão: 5 campos
    llm = ForbiddenLLM()

    document, stats = process(monkeypatch, user, pdf, llm)

    assert "header" not in llm.attempts
    assert stats["header_by_llm"] is False
    assert document.accession_number == "021-11111-222"
    assert document.patient_name_on_doc == "JOAO TESTE DA SILVA"


@pytest.mark.django_db
def test_sparse_header_falls_back_to_the_llm_header(monkeypatch, user, tmp_path):
    pdf = build_pdf(tmp_path / "esparso.pdf", [TABLE_BODY], identity=SPARSE_HEADER)
    llm = FakeLLMClient(header=SAMPLE_HEADER)  # a tabela é lida por regras: sem resultados do LLM

    document, stats = process(monkeypatch, user, pdf, llm)

    assert len(llm.header_calls) == 1
    assert "123.456.789-09" not in llm.header_calls[0]  # CPF/RG nunca chegam ao modelo
    assert stats["header_by_llm"] is True
    assert stats["llm_calls"] == 0  # o cabeçalho não conta como chamada de resultados
    assert document.accession_number == "021-11111-222"  # veio do LLM


def header_text(*fields: str) -> str:
    return "\n".join(fields)


FIELDS = [
    "Cliente: JOAO TESTE DA SILVA | O.S: 021-11111-222",
    "Data Nasc.: 05/03/1977",
    "Emissão: 31/08/2026 - 13:57:40",
    "Solicitante: DR EXEMPLO",
]


@pytest.mark.parametrize(
    ("count", "uses_llm"),
    [(1, True), (2, True), (3, False), (4, False), (5, False)],
)
def test_header_regex_threshold_is_three_fields(count, uses_llm):
    # `Cliente` + `O.S` na mesma linha contam como 2 campos; os demais, 1 cada.
    lines = {1: FIELDS[1:2], 2: FIELDS[0:1], 3: FIELDS[0:2], 4: FIELDS[0:3], 5: FIELDS}[count]
    header = pipeline.parse_header({}, header_text(*lines))

    found = pipeline.header_fields_found(header)

    assert found == count
    assert (found < pipeline.HEADER_REGEX_MIN_FIELDS) is uses_llm
