"""Pipeline de ponta a ponta com o layout "ficha" (sem `Material:`), sem LLM.

O texto das páginas é gravado direto no laudo (reprocessamento): o que se testa é segmentação,
cabeçalho por regex, regras determinísticas e normalização. Dados inventados.
"""

from datetime import date
from decimal import Decimal

import pytest

from apps.exams import tasks
from apps.exams.codes import ErrorCode, WarningCode
from apps.exams.extraction import pipeline
from apps.exams.extraction.fakes import FakeLLMClient
from apps.exams.models import (
    DocumentStatus,
    ExamDocument,
    ReferenceKind,
    ResultFlag,
    ResultOrigin,
    ReviewStatus,
)

from .ficha_fixtures import CHOLESTEROL, HDL, HEMOGRAM, HISTORY_PAGE_BODY, UREA, pages, received

pytestmark = pytest.mark.django_db

TRIGLYCERIDES = [
    "TRIGLICERIDES, soro",
    "Método: Ensaio enzimático",
    "RESULTADO | VALORES DE REFERÊNCIA",
    "92 mg/dL | Maior ou igual a 20 anos:",
    "Com jejum de 12 horas:",
    "Desejável: menor que 150 mg/dL",
    "Sem jejum de 12 horas:",
    "Desejável: menor que 175 mg/dL",
    "NOTA(1): A correta interpretação deste resultado é dependente das",
    *received(),
]
LDL = [
    "LDL-COLESTEROL, soro",
    "Método: Cálculo baseado na fórmula de Martin/Hopkins",
    "RESULTADO | VALORES DE REFERÊNCIA",
    "118 mg/dL | Maior ou igual a 20 anos:",
    "Com ou sem jejum de 12 horas:",
    "Ótimo | : menor que 100 mg/dL",
    "Desejável | : de 100 a 129 mg/dL",
    "Limítrofe | : de 130 a 159 mg/dL",
    "NOTA(1): Os valores de referência para LDL-colesterol",
    *received(),
]
SODIUM = [
    "SODIO, soro",
    "Método: Potenciométrico",
    "RESULTADO | VALORES DE REFERÊNCIA",
    "139 mEq/L | 136 a 145 mEq/L",
    *received(),
]
FERRITIN = [
    "FERRITINA, soro",
    "Método: Eletroquimioluminométrico",
    "RESULTADO | VALORES DE REFERÊNCIA",
    "163 microg/L | Sexo Masculino: de 26 a 446 microg/L",
    *received(),
]
HBA1C = [
    "HEMOGLOBINA GLICADA (A1C), sangue total",
    "Método: Imunoensaio de Inibição Turbidimétrica",
    "RESULTADO",
    "6,0 %",
    "Esse valor de hemoglobina glicada corresponde à glicemia média estimada",
    "de 126 mg/dL.",
    "VALORES DE REFERÊNCIA",
    "Indivíduos sem diabetes mellitus:",
    "Menor que 5,7% - baixo risco de desenvolvimento de diabetes",
    "5,7 a 6,4% - risco aumentado para diabetes",
    "RESULTADOS ANTERIORES:",
    "FICHA | DATA | RESULTADO",
    "========== | ========== | =========",
    "9990000002 | 19/08/2023 | 5,9",
    *received(),
]
CREATININE_AND_EGFR = [
    "CREATININA, soro",
    "Método: Cinético colorimétrico",
    "RESULTADO | VALOR DE REFERÊNCIA",
    "0,95 mg/dL | Acima de 12 anos: 0,70 a 1,30",
    "ESTIMATIVA DA FILTRAÇÃO GLOMERULAR | =================================",
    "Método: Cálculo da estimativa da taxa de filtração glomerular",
    "RESULTADO | VALOR DE REFERÊNCIA",
    "mL/min/1,73 m2 | mL/min/1,73 m2",
    "CKD-EPI 2021",
    *received(),
]
NARRATIVE = [
    "RM COLUNA LOMBO-SACRA",
    "TÉCNICA",
    "Sagital TSE ponderado em T2 e T1",
    "ANÁLISE",
    "Leve protrusão discal difusa em L4-L5 sem compressões radiculares.",
    "Laudado por: CRM 000/PE - DR. FULANO DE TAL",
    "LIBERADO EM: 07/12/2021 14:40",
    *received(),
]


def make_document(user, page_list) -> ExamDocument:
    return ExamDocument.objects.create(
        user=user,
        sha256="f" * 64,
        original_filename="laudo.pdf",
        page_count=len(page_list),
        page_text=[{"number": p.number, "method": p.method, "lines": p.lines} for p in page_list],
    )


@pytest.fixture
def llm(monkeypatch):
    client = FakeLLMClient()
    monkeypatch.setattr(tasks, "build_llm_client", lambda: client)
    return client


def run(user, *bodies):
    document = make_document(user, pages(*bodies))
    tasks.process_exam_document(document.pk, True)
    document.refresh_from_db()
    return document


def result(document, name, origin=ResultOrigin.CURRENT):
    return document.results.get(name_raw=name, origin=origin)


def codes(res):
    return {w["code"] for w in res.warnings}


def test_header_is_read_by_regex_without_the_llm(user, llm):
    document = run(user, CHOLESTEROL + UREA)

    assert llm.header_calls == []
    assert llm.result_calls == []
    assert document.accession_number == "9990000001"
    assert document.patient_name_on_doc == "JOAO TESTE DA SILVA"
    assert document.patient_birth_date == date(1977, 3, 5)
    assert document.ordering_physician == "DRA. FULANA DE TAL CRM 000PE"
    assert document.issued_at.date() == date(2025, 2, 10)
    assert document.collected_on == date(2025, 2, 10)
    assert document.status in (DocumentStatus.COMPLETED, DocumentStatus.NEEDS_REVIEW)
    assert WarningCode.PATIENT_MISMATCH not in {w["code"] for w in document.warnings}


def test_header_with_empty_physician_still_has_enough_fields(user, llm):
    document = make_document(user, pages(CHOLESTEROL, physician=""))
    tasks.process_exam_document(document.pk, True)
    document.refresh_from_db()

    assert llm.header_calls == []
    assert document.ordering_physician == ""
    assert document.accession_number == "9990000001"


def test_single_results_are_extracted_normalized_and_flagged(user, llm):
    document = run(user, CHOLESTEROL + UREA, HDL)

    cholesterol = result(document, "COLESTEROL TOTAL")
    assert cholesterol.value_numeric == Decimal("171")
    assert cholesterol.unit == "mg/dL"
    assert cholesterol.analyte.slug == "colesterol_total"
    assert cholesterol.ref_kind == ReferenceKind.UPPER
    assert cholesterol.ref_high == Decimal("190")
    assert cholesterol.flag == ResultFlag.NORMAL
    assert cholesterol.reference_raw.startswith("Maior ou igual a 20 anos:")
    assert cholesterol.method == "Ensaio enzimático"
    assert cholesterol.specimen == "soro"
    assert cholesterol.collected_at.date() == date(2025, 2, 10)

    urea = result(document, "UREIA")
    assert (urea.value_numeric, urea.ref_low, urea.ref_high) == (
        Decimal("27"),
        Decimal("10"),
        Decimal("50"),
    )
    assert result(document, "HDL-COLESTEROL").flag == ResultFlag.NORMAL
    assert document.results.filter(origin=ResultOrigin.CURRENT).count() == 3


def test_ambiguous_references_stay_printed_but_unparsed(user, llm):
    document = run(user, TRIGLYCERIDES, LDL)

    for name, value in (("TRIGLICERIDES", "92"), ("LDL-COLESTEROL", "118")):
        res = result(document, name)
        assert res.value_numeric == Decimal(value)
        assert res.ref_kind == ReferenceKind.NONE
        assert res.flag == ResultFlag.UNKNOWN
        assert res.ref_bands == []
        assert res.reference_raw != ""
        assert WarningCode.REFERENCE_NOT_PARSED in codes(res)
        assert res.review_status == ReviewStatus.AUTO  # só falta a faixa: informativo


def test_unambiguous_references_with_age_and_sex_qualifiers_are_parsed(user, llm):
    document = run(user, SODIUM + FERRITIN)

    sodium = result(document, "SODIO")
    assert (sodium.ref_low, sodium.ref_high, sodium.flag) == (
        Decimal("136"),
        Decimal("145"),
        ResultFlag.NORMAL,
    )
    ferritin = result(document, "FERRITINA")
    assert ferritin.unit == "µg/L"
    assert (ferritin.ref_low, ferritin.ref_high) == (Decimal("26"), Decimal("446"))


def test_result_alone_under_result_header_and_previous_results_table(user, llm):
    document = run(user, HBA1C)

    current = result(document, "HEMOGLOBINA GLICADA (A1C)")
    assert current.value_numeric == Decimal("6.0")
    assert current.unit == "%"
    assert current.analyte.slug == "hba1c"
    previous = document.results.filter(origin=ResultOrigin.LAB_PREVIOUS)
    assert [(r.collected_at.date(), r.value_numeric) for r in previous] == [
        (date(2023, 8, 19), Decimal("5.9"))
    ]


def test_hemogram_table_with_percent_and_absolute_differential(user, llm):
    document = run(user, HEMOGRAM)

    assert llm.result_calls == []
    erythrocytes = result(document, "ERITRÓCITOS")
    assert erythrocytes.unit == "milhões/µL"
    assert erythrocytes.value_numeric == Decimal("4.95")
    assert (erythrocytes.ref_low, erythrocytes.ref_high) == (Decimal("4.32"), Decimal("5.67"))
    assert erythrocytes.flag == ResultFlag.NORMAL

    rdw = result(document, "COEFICIENTE DE VARIAÇÃO DO VOLUME ERITROCITÁRIO (RDW)")
    assert rdw.analyte.slug == "rdw"
    chcm = result(document, "CONCENTRAÇÃO DE HEMOGLOBINA CORPUSCULAR MÉDIA")
    assert chcm.analyte.slug == "chcm"

    percent = result(document, "Neutrófilos")
    absolute = result(document, "Neutrófilos (absoluto)")
    assert (percent.analyte.slug, percent.unit, percent.value_numeric) == (
        "neutrofilos_pct",
        "%",
        Decimal("58.3"),
    )
    assert percent.reference_raw == "" and percent.ref_kind == ReferenceKind.NONE
    assert (absolute.analyte.slug, absolute.unit, absolute.value_numeric) == (
        "neutrofilos_abs",
        "/µL",
        Decimal("3620"),
    )
    assert (absolute.ref_low, absolute.ref_high) == (Decimal("1590"), Decimal("4770"))
    assert result(document, "LEUCÓCITOS").value_numeric == Decimal("6210")

    platelets = result(document, "TOTAL DE PLAQUETAS")
    assert (platelets.unit, platelets.value_numeric) == ("/µL", Decimal("231000"))
    assert (platelets.ref_low, platelets.ref_high) == (Decimal("151000"), Decimal("304000"))
    assert result(document, "VOLUME PLAQUETÁRIO MÉDIO").unit == "fL"
    assert platelets.section_raw == "PLAQUETAS"


def test_history_pages_never_produce_results(user, llm):
    document = run(user, CHOLESTEROL, HISTORY_PAGE_BODY, HISTORY_PAGE_BODY)

    assert [r.name_raw for r in document.results.all()] == ["COLESTEROL TOTAL"]
    assert llm.result_calls == []
    assert document.page_count == 3


def test_block_the_rules_cannot_read_goes_to_the_llm_and_keeps_the_hallucination_guard(user):
    client = FakeLLMClient(
        results=lambda text: {
            "results": [
                {
                    "name": "Creatinina",
                    "value_text": "9,99",  # valor que não existe no bloco
                    "unit": "mg/dL",
                    "reference_text": "",
                    "printed_flag": "",
                    "is_calculated": False,
                }
            ]
        }
    )
    document = make_document(user, pages(CHOLESTEROL, CREATININE_AND_EGFR))
    outcome = pipeline.process_document(document, client)

    assert outcome.stats["llm_calls"] == 1
    assert client.result_calls[0].startswith("CREATININA, soro")
    bad = document.results.get(name_raw="Creatinina")
    assert WarningCode.VALUE_NOT_IN_PAGE in codes(bad)
    assert bad.review_status == ReviewStatus.NEEDS_REVIEW
    assert document.results.filter(name_raw="COLESTEROL TOTAL").exists()


def test_narrative_report_without_numeric_results_fails_cleanly(user, llm):
    document = run(user, NARRATIVE)

    assert document.status == DocumentStatus.FAILED
    assert document.error_code == ErrorCode.NO_RESULTS_FOUND
    assert document.results.count() == 0


BILIRUBINS = [
    "BILIRRUBINAS, soro",
    "Método: Colorimétrico",
    "RESULTADO | VALORES DE REFERÊNCIA",
    "DIRETA : 0,45 mg/dL | 0,00 a 0,30 mg/dL",
    "INDIRETA: 0,51 mg/dL | 0,20 a 0,80 mg/dL",
    "TOTAL | : 0,88 mg/dL | 0,20 a 1,10 mg/dL",
    *received(),
]
TSH = [
    "HORMONIO TIROESTIMULANTE (TSH), soro",
    "Método: Ensaio eletroquimioluminométrico",
    "RESULTADO | VALORES DE REFERÊNCIA",
    "2,3 mUI/L | 20 - 59 a: 0,45 a 4,5 mUI/L",
    "NOTA(1): O uso de biotina em doses acima de 5.000 mcg/dia pode interferir.",
    *received(),
]


def test_generic_row_labels_resolve_to_the_right_analyte(user, llm):
    document = run(user, BILIRUBINS)

    slugs = {r.name_raw: r.analyte.slug for r in document.results.all()}
    assert slugs == {
        "BILIRRUBINAS DIRETA": "bilirrubina_direta",
        "BILIRRUBINAS INDIRETA": "bilirrubina_indireta",
        "BILIRRUBINAS TOTAL": "bilirrubina_total",
    }
    assert result(document, "BILIRRUBINAS TOTAL").flag == ResultFlag.NORMAL
    assert result(document, "BILIRRUBINAS DIRETA").flag == ResultFlag.HIGH


def test_age_abbreviated_with_a_reads_the_reference(user, llm):
    document = run(user, TSH)

    tsh = result(document, "HORMONIO TIROESTIMULANTE (TSH)")
    assert (tsh.ref_low, tsh.ref_high, tsh.flag) == (
        Decimal("0.45"),
        Decimal("4.5"),
        ResultFlag.NORMAL,
    )
    assert tsh.value_canonical == Decimal("2.3")  # mUI/L = µUI/mL
    assert WarningCode.UNIT_NOT_CONVERTIBLE not in codes(tsh)


def test_units_written_differently_convert_to_the_catalog_unit(user, llm):
    document = run(user, SODIUM + FERRITIN)

    assert WarningCode.UNIT_NOT_CONVERTIBLE not in codes(result(document, "SODIO"))
    assert result(document, "FERRITINA").value_canonical == Decimal("163")
    assert document.status == DocumentStatus.COMPLETED
