"""Divisão em pedaços do que vai ao LLM, progresso por unidade de trabalho e a escolha
entre regras e LLM. Tudo com fakes: o Ollama nunca é chamado."""

import pytest

from apps.exams.codes import WarningCode
from apps.exams.extraction import pipeline
from apps.exams.extraction.analytes import AnalyteResolver
from apps.exams.extraction.blocks import Block, split_blocks
from apps.exams.extraction.fakes import FakeLLMClient
from apps.exams.extraction.linearize import PageText
from apps.exams.models import ExamDocument, ResultOrigin

from .conftest import SAMPLE_HEADER, sample_row

pytestmark = pytest.mark.django_db

TABLE_ROW_COUNT = 26


def table_lines(count: int = TABLE_ROW_COUNT) -> list[str]:
    # A primeira linha traz uma referência em texto livre: as regras de tabela não leem o
    # bloco inteiro (nunca meia tabela) e ele segue para o LLM, que é o que estes testes cobrem.
    return [
        f"Analito{i:02d} | {i},50 g/dL | "
        + ("conforme a idade" if i == 1 else f"{i},00 a {i + 10},00 g/dL")
        for i in range(1, count + 1)
    ]


def table_block(count: int = TABLE_ROW_COUNT, title: str = "HEMOGRAMA") -> list[str]:
    return [title, "Material: sangue", "Método: Automatizado", *table_lines(count)]


GLUCOSE = [
    "GLICOSE | 103 mg/dL",
    "Material: soro",
    "Data da coleta: 25/08/2026 - 07:01:51",
    "Valor de Referência: Normal: entre 70 a 99 mg/dL",
]
SODIUM = [
    "SÓDIO | 140 mmol/L",
    "Material: soro",
    "Data da coleta: 25/08/2026 - 07:01:51",
    "Valor de Referência: 135 a 145 mmol/L",
]


class CountingLLM(FakeLLMClient):
    """Responde cada linha `AnalitoNN | ...` e registra quantas linhas cada chamada viu."""

    def __init__(self, echo_title: bool = False, **kwargs):
        super().__init__(header=SAMPLE_HEADER, results=self._respond, **kwargs)
        self.echo_title = echo_title
        self.rows_per_call: list[int] = []

    def _respond(self, text: str) -> dict:
        rows = []
        lines = text.split("\n")
        if self.echo_title and " | " in lines[0]:
            title, _, rest = lines[0].partition(" | ")
            value, _, unit = rest.partition(" ")
            rows.append(sample_row(title, value, unit, source_line=lines[0]))
        for line in lines:
            if line.startswith("Analito"):
                name, value_unit, reference = line.split(" | ")
                value, _, unit = value_unit.partition(" ")
                rows.append(sample_row(name, value, unit, reference, source_line=line))
        self.rows_per_call.append(sum(1 for line in lines if line.startswith("Analito")))
        return {"results": rows}


def analyze(user, *blocks: list[str], llm=None, on_progress=None):
    """Roda `analyze` sobre blocos prontos (uma página, sem PDF)."""
    lines = [line for block in blocks for line in block]
    document = ExamDocument.objects.create(
        user=user, sha256="a" * 64, original_filename="x.pdf", page_count=1
    )
    llm = llm or CountingLLM()
    pages = [PageText(1, lines, "text")]
    return pipeline.analyze(document, pages, llm, on_progress), llm


# --- divisão em pedaços -------------------------------------------------------------------


def test_small_block_goes_whole():
    block = Block(title="X", lines=table_block(8), page=1)
    (chunk,) = pipeline.split_for_llm(block)
    assert chunk.lines == block.lines
    assert chunk.origin == list(range(11))


def test_block_at_the_limit_goes_whole():
    block = Block(title="X", lines=["T", *table_lines(pipeline.MAX_LLM_BLOCK_LINES - 1)], page=1)
    assert len(pipeline.split_for_llm(block)) == 1


def test_large_table_is_split_with_title_and_meta_lines_in_every_chunk():
    block = Block(title="HEMOGRAMA", lines=table_block(), page=1)
    chunks = pipeline.split_for_llm(block)

    assert len(chunks) == 3
    for chunk in chunks:
        assert chunk.lines[:3] == ["HEMOGRAMA", "Material: sangue", "Método: Automatizado"]
        body = chunk.lines[3:]
        assert 0 < len(body) <= pipeline.MAX_CHUNK_LINES
        # cada linha do pedaço aponta para a mesma linha no bloco original
        assert [block.lines[i] for i in chunk.origin] == chunk.lines
    covered = [i for chunk in chunks for i in chunk.origin[3:]]
    assert covered == list(range(3, 3 + TABLE_ROW_COUNT))
    assert [chunk.first for chunk in chunks] == [True, False, False]


def test_block_without_a_title_has_no_repeated_title():
    block = Block(title="", lines=table_lines(30), page=1)
    chunks = pipeline.split_for_llm(block)
    assert [c.lines for c in chunks] == [table_lines(30)[i : i + 10] for i in (0, 10, 20)]


# --- LLM: um bloco grande vira várias chamadas pequenas ------------------------------------


def test_26_row_table_makes_three_calls_of_at_most_12_rows(user):
    analysis, llm = analyze(user, table_block())

    assert len(llm.result_calls) == 3
    assert llm.rows_per_call == [9, 9, 8]
    assert max(llm.rows_per_call) <= pipeline.MAX_CHUNK_LINES
    assert sum(llm.rows_per_call) == TABLE_ROW_COUNT
    assert analysis.stats["llm_calls"] == 3
    assert analysis.stats["rule_blocks"] == 0
    assert analysis.stats["blocks"] == 1

    drafts = [d for d in analysis.drafts if d["origin"] == ResultOrigin.CURRENT]
    assert len(drafts) == TABLE_ROW_COUNT
    # source_line aponta para a linha do bloco original, não para o pedaço
    assert [d["source_line"] for d in drafts] == table_lines()


def test_title_echoed_by_the_model_in_every_chunk_yields_one_row(user):
    # O título tem valor (PERFIL | 5 g/dL) mas o corpo é uma tabela: vai ao LLM.
    block = table_block(title="PERFIL | 5 g/dL")
    assert pipeline._plan(Block(title=block[0], lines=block, page=1)).kind == "llm"

    analysis, llm = analyze(user, block, llm=CountingLLM(echo_title=True))

    assert len(llm.result_calls) == 3
    names = [d["name_raw"] for d in analysis.drafts]
    assert names.count("PERFIL") == 1
    assert len(names) == TABLE_ROW_COUNT + 1


def test_table_block_whose_title_line_has_a_value_is_not_read_by_rules(user):
    lines = ["PERFIL | 5 g/dL", "Material: sangue", *table_lines(5)]
    split = split_blocks([lines])[1]
    assert pipeline._plan(split[0]).kind == "llm"
    analysis, llm = analyze(user, lines)
    assert analysis.stats["rule_blocks"] == 0
    assert len(llm.result_calls) == 1


# --- progresso e estatísticas -------------------------------------------------------------


def test_progress_total_counts_rule_blocks_and_llm_chunks(user):
    calls: list[tuple[int, int]] = []
    analysis, _ = analyze(
        user,
        GLUCOSE,
        table_block(),
        SODIUM,
        on_progress=lambda done, total: calls.append((done, total)),
    )

    # 2 blocos por regras + 3 pedaços do LLM
    assert calls == [(i, 5) for i in range(6)]
    assert analysis.stats["blocks"] == 3
    assert analysis.stats["rule_blocks"] == 2
    assert analysis.stats["llm_calls"] == 3
    assert analysis.stats["results"] == TABLE_ROW_COUNT + 2


def test_rule_only_report_never_calls_the_llm(user):
    analysis, llm = analyze(user, GLUCOSE, SODIUM)

    assert llm.result_calls == []
    assert analysis.stats["rule_blocks"] == 2
    assert analysis.stats["llm_calls"] == 0
    assert analysis.stats["empty_blocks"] == 0
    names = {d["name_raw"] for d in analysis.drafts}
    assert names == {"GLICOSE", "SÓDIO"}


def test_rule_rows_go_through_the_same_normalisation_as_llm_rows(user):
    analysis, _ = analyze(user, GLUCOSE)
    (glucose,) = analysis.drafts
    assert glucose["value_numeric"] == 103
    assert glucose["unit"] == "mg/dL"
    assert glucose["analyte"].slug == "glicose"
    assert glucose["flag"] == "high"
    assert glucose["source_line"] == "GLICOSE | 103 mg/dL"
    assert glucose["reference_raw"] == "Normal: entre 70 a 99 mg/dL"


def test_build_draft_still_checks_the_value_against_the_block_text(user):
    # Mesma checagem para linhas de regras e do LLM: o valor tem de existir no bloco.
    row = {**GLUCOSE_ROW, "value_text": "999"}
    block = Block(title="GLICOSE", lines=GLUCOSE, page=1)
    draft = pipeline.build_draft(
        row,
        block,
        resolver=AnalyteResolver(FakeLLMClient(), user=user),
        patient=pipeline.PatientContext(sex="male", birth_date=None),
        fallback_collected=None,
        vision=False,
    )
    assert WarningCode.VALUE_NOT_IN_PAGE in {w["code"] for w in draft["warnings"]}


GLUCOSE_ROW = {
    "name": "GLICOSE",
    "value_text": "103",
    "unit": "mg/dL",
    "reference_text": "",
    "printed_flag": "",
    "is_calculated": False,
}


# --- referência copiada por código quando o modelo a deixa vazia -----------------------------


QUALITATIVE = [
    "PESQUISA DE ANTICORPOS | Reagente",
    "Material: soro",
    "Data da coleta: 25/08/2026 - 07:01:51",
    "Valor de Referência: | Não reagente",
]


def test_empty_reference_from_the_model_is_filled_from_the_block(user):
    llm = FakeLLMClient(
        header=SAMPLE_HEADER,
        results=lambda text: {
            "results": [
                sample_row("PESQUISA DE ANTICORPOS", "Reagente", "", "", source_line=QUALITATIVE[0])
            ]
        },
    )
    analysis, _ = analyze(user, QUALITATIVE, llm=llm)
    (draft,) = analysis.drafts
    assert draft["reference_raw"] == "Não reagente"


def test_reference_given_by_the_model_is_kept(user):
    llm = FakeLLMClient(
        header=SAMPLE_HEADER,
        results=lambda text: {
            "results": [sample_row("PESQUISA DE ANTICORPOS", "Reagente", "", "Negativo")]
        },
    )
    analysis, _ = analyze(user, QUALITATIVE, llm=llm)
    (draft,) = analysis.drafts
    assert draft["reference_raw"] == "Negativo"


def test_reference_is_not_borrowed_from_a_later_row_of_the_same_chunk(user):
    lines = [
        "PAINEL",
        "Material: soro",
        "Exame A: | Positivo",
        "Exame B: | Negativo",
        "Valor de Referência: | Negativo",
    ]
    llm = FakeLLMClient(
        header=SAMPLE_HEADER,
        results=lambda text: {
            "results": [
                sample_row("Exame A", "Positivo", "", "", source_line=lines[2]),
                sample_row("Exame B", "Negativo", "", "", source_line=lines[3]),
            ]
        },
    )
    analysis, _ = analyze(user, lines, llm=llm)
    by_name = {d["name_raw"]: d for d in analysis.drafts}
    assert by_name["Exame A"]["reference_raw"] == ""
    assert by_name["Exame B"]["reference_raw"] == "Negativo"


def test_empty_answer_for_a_single_chunk_block_is_retried_once(user):
    llm = FakeLLMClient(header=SAMPLE_HEADER)
    analysis, _ = analyze(user, QUALITATIVE, llm=llm)
    assert len(llm.result_calls) == 2  # a segunda chance, como antes
    assert analysis.stats["llm_calls"] == 2
    assert analysis.stats["empty_blocks"] == 1


def test_empty_chunks_of_a_large_block_are_not_retried(user):
    llm = FakeLLMClient(header=SAMPLE_HEADER)
    analysis, _ = analyze(user, table_block(), llm=llm)
    assert len(llm.result_calls) == 3
    assert analysis.stats["llm_calls"] == 3
