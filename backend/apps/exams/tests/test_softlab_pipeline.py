"""Pipeline de ponta a ponta com o layout "Softl@b", sem LLM (exceto onde dito).

O texto das páginas é gravado direto no laudo (reprocessamento): o que se testa é segmentação,
cabeçalho por regex, regras determinísticas e normalização. Dados inventados.
"""

from datetime import date
from decimal import Decimal

import pytest
from django.utils import timezone

from apps.exams import tasks
from apps.exams.codes import WarningCode
from apps.exams.extraction.fakes import FakeLLMClient
from apps.exams.models import (
    DocumentStatus,
    ExamDocument,
    ReferenceKind,
    ResultFlag,
    ResultOrigin,
    ReviewStatus,
)

from .softlab_fixtures import (
    ALBUMIN,
    BIOAVAILABLE_TESTOSTERONE,
    COVID,
    CREATININE,
    ESTRADIOL,
    GLUCOSE,
    HBA1C,
    HDL_AFTER_BOTH_ROWS,
    HEMOGRAM,
    LH,
    ORDER,
    PHYSICIAN,
    PSA,
    SHBG,
    T4,
    TESTOSTERONE,
    TOTAL_PROTEIN,
    TSH,
    VDRL,
    pages,
)

pytestmark = pytest.mark.django_db


def make_document(user, page_list) -> ExamDocument:
    return ExamDocument.objects.create(
        user=user,
        sha256="e" * 64,
        original_filename="laudo.pdf",
        page_count=len(page_list),
        page_text=[{"number": p.number, "method": p.method, "lines": p.lines} for p in page_list],
    )


@pytest.fixture
def llm(monkeypatch):
    client = FakeLLMClient()
    monkeypatch.setattr(tasks, "build_llm_client", lambda: client)
    return client


def run(user, *bodies, **kwargs):
    document = make_document(user, pages(*bodies, **kwargs))
    tasks.process_exam_document(document.pk, True)
    document.refresh_from_db()
    return document


def result(document, name, origin=ResultOrigin.CURRENT):
    return document.results.get(name_raw=name, origin=origin)


def codes(res):
    return {w["code"] for w in res.warnings}


def test_header_is_read_by_regex_without_the_llm_and_without_birth_date_or_sex(user, llm):
    document = run(user, ALBUMIN + TSH)

    assert llm.header_calls == []
    assert llm.result_calls == []
    assert document.accession_number == ORDER
    assert document.patient_name_on_doc == "JOAO TESTE DA SILVA"
    assert document.patient_birth_date is None
    assert document.ordering_physician == PHYSICIAN
    assert document.issued_at.date() == date(2024, 3, 12)
    assert document.collected_on == date(2024, 3, 12)
    assert document.status in (DocumentStatus.COMPLETED, DocumentStatus.NEEDS_REVIEW)
    assert WarningCode.PATIENT_MISMATCH not in {w["code"] for w in document.warnings}
    assert document.page_count == 1


def test_issue_date_at_the_bottom_of_every_page_is_still_found(user, llm):
    document = run(user, ALBUMIN, TSH, cadastro_on_top=False)

    assert llm.header_calls == []
    assert document.issued_at.date() == date(2024, 3, 12)
    assert document.accession_number == ORDER


def test_single_results_are_normalized_with_unit_spellings_and_the_patient_profile(user, llm):
    document = run(user, ALBUMIN + CREATININE + SHBG + TESTOSTERONE)

    albumin = result(document, "ALBUMINA")
    assert albumin.value_numeric == Decimal("3.9")
    assert albumin.unit == "g/dL"
    assert albumin.analyte.slug == "albumina"
    assert albumin.ref_kind == ReferenceKind.RANGE
    assert (albumin.ref_low, albumin.ref_high, albumin.flag) == (
        Decimal("3.2"),
        Decimal("4.8"),
        ResultFlag.NORMAL,
    )
    assert albumin.reference_raw == "3.2 A 4.8 g/dl"
    assert albumin.method == "COLORIMÉTRICO - EXEMPLO"
    assert albumin.specimen == "SORO"
    assert albumin.collected_at.date() == date(2024, 3, 12)
    assert albumin.review_status == ReviewStatus.AUTO

    # o perfil do usuário é homem: a faixa de homens vale
    creatinine = result(document, "CREATININA")
    assert (creatinine.ref_low, creatinine.ref_high) == (Decimal("0.7"), Decimal("1.3"))
    assert creatinine.flag == ResultFlag.NORMAL
    shbg = result(document, "SHBG - GLOBULINA LIGADORA DE HORMÔNIOS SEXUAIS")
    assert (shbg.ref_low, shbg.ref_high, shbg.flag) == (
        Decimal("14.5"),
        Decimal("48.4"),
        ResultFlag.LOW,
    )

    testosterone = result(document, "TESTOSTERONA")
    assert testosterone.unit == "ng/dL"  # ng/100ml é a mesma unidade
    assert testosterone.value_canonical == Decimal("512.0")
    assert testosterone.ref_kind == ReferenceKind.NONE  # matriz por sexo e idade: só o texto
    assert testosterone.reference_raw.startswith("MULHER ADULTA:6,0 A 82,0 ng/100ml.")  # impresso
    assert WarningCode.REFERENCE_NOT_PARSED in codes(testosterone)
    assert testosterone.review_status == ReviewStatus.AUTO


def test_each_result_carries_its_own_previous_values(user, llm):
    document = run(user, LH + TSH + PSA)

    previous = {
        (r.analyte.slug, r.collected_at.date()): r.value_numeric
        for r in document.results.filter(origin=ResultOrigin.LAB_PREVIOUS)
    }
    assert previous == {
        ("lh", date(2023, 8, 2)): Decimal("3.18"),
        ("lh", date(2022, 11, 11)): Decimal("5.43"),
        ("tsh", date(2022, 11, 11)): Decimal("1.87"),
        ("psa_total", date(2023, 8, 2)): Decimal("0.94"),
        ("psa_livre", date(2023, 8, 2)): Decimal("0.26"),
    }
    assert result(document, "LH - ESPECÍFICO").value_numeric == Decimal("6.27")


def test_reference_with_an_average_clause_gives_the_range(user, llm):
    document = run(user, T4)

    t4 = result(document, "T4 LIVRE")
    assert (t4.unit, t4.analyte.slug) == ("ng/dL", "t4_livre")
    assert (t4.ref_low, t4.ref_high, t4.flag) == (
        Decimal("0.77"),
        Decimal("2.19"),
        ResultFlag.NORMAL,
    )


def test_matrix_reference_leaves_the_printed_text_and_no_flag(user, llm):
    document = run(user, ESTRADIOL)

    estradiol = result(document, "ESTRADIOL")
    assert estradiol.flag == ResultFlag.UNKNOWN
    assert estradiol.ref_bands == []
    assert estradiol.reference_raw.splitlines()[0] == "HOMENS: PRÉ-PUBERES: 2.0 A 8.0 pg/ml"
    assert estradiol.collected_at.date() == date(2024, 3, 12)
    assert timezone.localtime(estradiol.collected_at).hour == 7


def test_hemogram_counts_use_the_thousands_dot_and_sex_columns_use_the_profile(user, llm):
    document = run(user, HEMOGRAM)

    assert llm.result_calls == []
    assert document.results.filter(origin=ResultOrigin.CURRENT).count() == 12
    leukocytes = result(document, "LEUCÓCITOS")
    assert (leukocytes.unit, leukocytes.value_numeric) == ("/µL", Decimal("7200"))
    assert (leukocytes.ref_low, leukocytes.ref_high) == (Decimal("4000"), Decimal("11000"))
    platelets = result(document, "CONTAGEM DE PLAQUETAS")
    assert platelets.value_numeric == Decimal("238000")
    assert platelets.analyte.slug == "plaquetas"
    percent = result(document, "SEGMENTADOS")
    absolute = result(document, "SEGMENTADOS (absoluto)")
    assert (percent.analyte.slug, percent.value_numeric) == ("segmentados_pct", Decimal("63"))
    assert (absolute.analyte.slug, absolute.value_numeric) == ("segmentados_abs", Decimal("4536"))
    assert (absolute.ref_low, absolute.ref_high) == (Decimal("2310"), Decimal("7150"))

    hemacias = result(document, "HEMÁCIAS")
    assert (hemacias.unit, hemacias.value_numeric) == ("milhões/µL", Decimal("5.02"))
    assert (hemacias.ref_low, hemacias.ref_high, hemacias.flag) == (
        Decimal("4.7"),
        Decimal("6.1"),
        ResultFlag.NORMAL,
    )
    vcm = result(document, "VCM")
    assert (vcm.unit, vcm.analyte.slug, vcm.value_numeric) == ("fL", "vcm", Decimal("88.1"))
    mpv = result(document, "MPV")
    assert mpv.flag == ResultFlag.HIGH
    blasts = result(document, "BLASTOS")
    assert blasts.analyte.slug == "blastos_pct"  # sem apelido próprio viraria "monócitos"


def test_panel_rows_keep_their_own_references_and_the_unit_comes_from_the_label(user, llm):
    document = run(user, HBA1C + PSA)

    a1c = result(document, "% HEMOGLOBINA A1C")
    assert (a1c.analyte.slug, a1c.unit, a1c.value_numeric) == ("hba1c", "%", Decimal("5.4"))
    assert (a1c.ref_low, a1c.ref_high, a1c.flag) == (
        Decimal("4.0"),
        Decimal("6.5"),
        ResultFlag.NORMAL,
    )
    average = result(document, "GLICEMIA MÉDIA ESTIMADA")
    assert average.reference_raw == ""
    psa = result(document, "PSA TOTAL")
    assert (psa.ref_high, psa.flag) == (Decimal("4"), ResultFlag.NORMAL)
    assert result(document, "PSA LIVRE").reference_raw == ""
    ratio = result(document, "RELAÇÃO PSA LIVRE / PSA TOTAL")
    assert ratio.value_numeric == Decimal("18.7")
    assert ratio.review_status == ReviewStatus.NEEDS_REVIEW  # sem exame no catálogo
    assert WarningCode.UNMAPPED_ANALYTE in codes(ratio)


def test_hdl_and_its_percent_get_their_own_exams(user, llm):
    document = run(user, HDL_AFTER_BOTH_ROWS)

    assert result(document, "COLESTEROL HDL").analyte.slug == "colesterol_hdl"
    percent = result(document, "COLESTEROL HDL(%)")
    assert (percent.analyte.slug, percent.unit) == ("colesterol_hdl_pct", "%")
    assert codes(percent) == {WarningCode.REFERENCE_NOT_PARSED} or codes(percent) == set()


def test_qualitative_results_and_the_comparator_free_value_text(user, llm):
    document = run(user, COVID + VDRL)

    igg = result(document, "ANTICORPOS IgG")
    assert igg.value_numeric is None
    assert igg.value_text == "NEGATIVO"
    assert igg.reference_raw == "NEGATIVO"
    assert igg.flag == ResultFlag.NORMAL
    igm = result(document, "ANTICORPOS IgM")
    assert igm.value_text == "POSITIVO"
    assert igm.flag == ResultFlag.UNKNOWN  # sem referência impressa na linha
    assert result(document, "SOROLOGIA PARA LUES - VDRL").value_text == "NEGATIVO"


def test_ambiguous_thousands_dot_outside_counts_is_marked_for_review(user, llm):
    body = ["ALFA EXEMPLO | 1.234 mg/dl", "VALORES DE REFERÊNCIA: 1.0 A 2.0 mg/dl", ALBUMIN[-1]]

    document = run(user, body)

    alpha = result(document, "ALFA EXEMPLO")
    assert WarningCode.AMBIGUOUS_NUMBER_FORMAT in codes(alpha)
    assert alpha.review_status == ReviewStatus.NEEDS_REVIEW


def test_comparator_in_the_result_is_kept(user, llm):
    body = ["PCR ULTRASSENSÍVEL | < 0.30 mg/l.", "VALOR DE REFERÊNCIA: < 5,0 mg/l", ALBUMIN[-1]]

    document = run(user, body)

    pcr = result(document, "PCR ULTRASSENSÍVEL")
    assert pcr.comparator == "lt"
    assert pcr.value_numeric == Decimal("0.30")
    assert pcr.analyte.slug == "pcr"
    assert pcr.flag == ResultFlag.NORMAL


def test_reference_and_unit_typos_do_not_hide_the_result(user, llm):
    document = run(user, GLUCOSE + TOTAL_PROTEIN)

    assert (result(document, "GLICOSE (GLICEMIA JEJUM)").flag) == ResultFlag.NORMAL
    protein = result(document, "PROTEÍNAS TOTAIS")
    assert protein.unit == "g/dL"
    assert protein.analyte.slug == "proteinas_totais"
    assert protein.reference_raw == "5.7 A 8.2 mg/dl"
    assert protein.ref_kind == ReferenceKind.NONE


def test_block_the_rules_do_not_read_goes_whole_to_the_llm_and_nothing_is_half_emitted(
    user, monkeypatch
):
    seen: list[str] = []

    def answer(text: str) -> dict:
        seen.append(text)
        return {"results": []}

    monkeypatch.setattr(tasks, "build_llm_client", lambda: FakeLLMClient(results=answer))

    document = run(user, ALBUMIN + BIOAVAILABLE_TESTOSTERONE)

    names = list(
        document.results.filter(origin=ResultOrigin.CURRENT).values_list("name_raw", flat=True)
    )
    assert names == ["ALBUMINA"]  # o painel ilegível não deixou nenhuma linha pela metade
    assert len(seen) == 1
    assert seen[0].splitlines()[0].startswith("TESTOSTERONA LIVRE CALCULADA")
    assert "TESTOSTERONA BIODISPONÍVEL(BaT) | 301.0 | ng/dl | 61.0 %" in seen[0]
    assert "Pedido:" not in seen[0] and "CRM-PJ" not in seen[0]  # cabeçalho/rodapé não vão junto
