import re
from decimal import Decimal
from pathlib import Path

import pytest
from django.utils import timezone

from apps.exams import tasks
from apps.exams.codes import ErrorCode, WarningCode
from apps.exams.extraction.fakes import FakeLLMClient
from apps.exams.models import (
    Analyte,
    DocumentStatus,
    ExamResult,
    ExtractionRun,
    ResultOrigin,
    ReviewStatus,
)

from .conftest import (
    HEADER_WITHOUT_SITE,
    PAGE_1,
    SAMPLE_HEADER,
    SPARSE_HEADER,
    build_pdf,
    create_document,
    header_with_accession,
    qualitative_block,
    row,
    sample_llm_results,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def fake_llm(monkeypatch):
    llm = FakeLLMClient(header=SAMPLE_HEADER, results=sample_llm_results)
    monkeypatch.setattr(tasks, "build_llm_client", lambda: llm)
    return llm


def old_glucose_pdf(path: Path, collected: str, accession: str = "021-00000-001") -> Path:
    """Laudo antigo só com glicose, coletada em `collected` (a data vem do bloco)."""
    body = [
        row("GLICOSE", "", "", "92 mg/dL"),
        [(40, "Material: soro")],
        [(40, f"Data da coleta: {collected}")],
        [(40, "Valor de Referência: Normal: Glicemia de jejum entre 70 a 99 mg/dL")],
        [(40, "Liberado por: Dr. Teste CRM-CE 1")],
    ]
    return build_pdf(path, [body], identity=header_with_accession(accession))


def by_name(document, name, origin=ResultOrigin.CURRENT):
    return document.results.get(name_raw=name, origin=origin)


def warning_codes(result):
    return {w["code"] for w in result.warnings}


def test_end_to_end_extraction(user, sample_pdf, fake_llm):
    document = create_document(user, sample_pdf)
    stored_path = Path(document.upload_path)
    assert stored_path.exists()

    tasks.process_exam_document(document.pk)
    document.refresh_from_db()

    # O PDF é descartado ao fim da extração; o texto fica para reprocessar.
    assert not stored_path.exists()
    assert document.upload_path == ""
    assert document.page_count == 3
    assert document.page_text

    # Cabeçalho lido por regex (sem LLM); o laboratório vem do domínio do site impresso
    assert document.laboratory.name == "Exemplo"
    assert document.accession_number == "021-11111-222"
    assert document.collected_on.isoformat() == "2026-08-25"
    assert document.status == DocumentStatus.NEEDS_REVIEW  # há linhas para revisar

    run = document.runs.get()
    assert run.status == "success"
    assert run.model == "fake-model"
    assert run.stats["results"] == 11
    assert run.prompt_version == "2026-10-3"
    # Cinco exames de um resultado só saem por regras; só a tabela do hemograma vai ao LLM.
    assert run.stats["rule_blocks"] == 5
    assert run.stats["llm_calls"] == 1
    assert run.stats["blocks"] == 6

    # Glicose: faixa "alterada" identificada e sinalizada como acima da referência
    glucose = by_name(document, "GLICOSE")
    assert glucose.analyte.slug == "glicose"
    assert glucose.value_numeric == Decimal("103")
    assert glucose.flag == "high"
    assert glucose.interpretation == "Glicemia de jejum alterada"
    assert (glucose.ref_low, glucose.ref_high) == (Decimal("70"), Decimal("99"))
    assert glucose.review_status == ReviewStatus.AUTO
    # Derivados em código a partir do bloco (o modelo só transcreve 6 campos)
    assert glucose.source_line == "GLICOSE | 103 mg/dL"
    assert glucose.section_raw == ""
    assert glucose.specimen == "soro"
    assert glucose.method == "Hexoquinase"
    assert timezone.localtime(glucose.collected_at).strftime("%d/%m/%Y %H:%M:%S") == (
        "25/08/2026 07:01:51"
    )

    # Seção = título em CAIXA ALTA que precede a linha; source_line = a linha impressa
    red_cells = by_name(document, "Hemácias")
    assert red_cells.section_raw == "ERITROGRAMA"
    assert red_cells.source_line == "Hemácias | 4,85 | milhões/uL | 4,50 a 6,50 milhões/uL"
    assert red_cells.method == "Automatizado"
    assert red_cells.specimen == "sangue"
    assert by_name(document, "Hemoglobina").section_raw == "ERITROGRAMA"
    assert by_name(document, "Leucócitos").section_raw == "LEUCOGRAMA"
    assert by_name(document, "Neutrófilos").section_raw == "LEUCOGRAMA"
    assert "2.400 /uL" in by_name(document, "Neutrófilos (absoluto)").source_line
    assert red_cells.notes == ""

    # Milhar com ponto resolvido pela faixa plausível (4.000 /µL, não 4,0)
    leukocytes = by_name(document, "Leucócitos")
    assert leukocytes.value_numeric == Decimal("4000")
    assert leukocytes.unit == "/µL"
    assert WarningCode.AMBIGUOUS_NUMBER_FORMAT not in warning_codes(leukocytes)

    # Percentual e absoluto são exames distintos, decididos pela unidade
    assert by_name(document, "Neutrófilos").analyte.slug == "neutrofilos_pct"
    absolute = by_name(document, "Neutrófilos (absoluto)")
    assert absolute.analyte.slug == "neutrofilos_abs"
    # Contagem por µL é inteira: "2.400" é milhar, sem aviso de formato ambíguo
    assert absolute.value_numeric == Decimal("2400")
    assert WarningCode.AMBIGUOUS_NUMBER_FORMAT not in warning_codes(absolute)
    assert absolute.review_status == ReviewStatus.AUTO

    # Valor ausente nunca é descartado em silêncio
    hemoglobin = by_name(document, "Hemoglobina")
    assert hemoglobin.value_numeric is None
    assert hemoglobin.review_status == ReviewStatus.NEEDS_REVIEW
    assert WarningCode.VALUE_MISSING in warning_codes(hemoglobin)

    # Bloco lido por regras: o valor sai do próprio texto (nada a alucinar) e a referência
    # é copiada do bloco, sem a linha do laboratório de apoio.
    magnesium = by_name(document, "MAGNÉSIO")
    assert magnesium.value_numeric == Decimal("2.1")
    assert magnesium.review_status == ReviewStatus.AUTO
    assert WarningCode.VALUE_NOT_IN_PAGE not in warning_codes(magnesium)
    assert magnesium.reference_raw == "DE 1,6 A 2,6 mg/dL"
    assert magnesium.performing_lab == "HERMES PARDINI MEDICINA DIAGNOSTICA"
    assert magnesium.source_line == "MAGNÉSIO | 2,1 mg/dL"

    # Faixa por sexo (perfil: masculino) e por idade (49 anos na coleta)
    creatinine = by_name(document, "CREATININA")
    assert (creatinine.ref_low, creatinine.ref_high) == (Decimal("0.70"), Decimal("1.30"))
    assert creatinine.flag == "normal"
    free_t = by_name(document, "TESTOSTERONA LIVRE CALCULADA")
    assert (free_t.ref_low, free_t.ref_high) == (Decimal("3.03"), Decimal("14.80"))
    assert free_t.flag == "normal"

    # eRFG: exame calculado, "> 90" exclusivo -> abaixo da referência
    erfg = by_name(document, "eRFG")
    assert erfg.analyte.is_calculated
    assert erfg.flag == "low"

    # "Anteriores" viram histórico (herdam unidade; eRFG calculado não gera)
    previous = ExamResult.objects.filter(document=document, origin=ResultOrigin.LAB_PREVIOUS)
    glucose_prev = previous.filter(analyte__slug="glicose").order_by("collected_at")
    assert [r.value_numeric for r in glucose_prev] == [Decimal("84"), Decimal("92")]
    assert all(r.unit == "mg/dL" for r in glucose_prev)
    assert not previous.filter(analyte__slug="erfg").exists()
    # TSH anterior "1.251" é decimal (1251 µUI/mL seria implausível)
    tsh_prev = previous.get(analyte__slug="tsh", collected_at__year=2016)
    assert tsh_prev.value_numeric == Decimal("1.251")


def test_value_missing_from_the_block_text_is_flagged_on_the_llm_path(
    user, sample_pdf, monkeypatch
):
    # Alucinação: o modelo devolve 4,99, mas o hemograma imprime 4,85.
    def hallucinating(text: str) -> dict:
        output = sample_llm_results(text)
        for item in output["results"]:
            if item["name"] == "Hemácias":
                item["value_text"] = "4,99"
        return output

    llm = FakeLLMClient(header=SAMPLE_HEADER, results=hallucinating)
    monkeypatch.setattr(tasks, "build_llm_client", lambda: llm)
    document = create_document(user, sample_pdf)
    tasks.process_exam_document(document.pk)

    red_cells = by_name(document, "Hemácias")
    assert red_cells.review_status == ReviewStatus.NEEDS_REVIEW
    assert WarningCode.VALUE_NOT_IN_PAGE in warning_codes(red_cells)
    assert red_cells.source_line == ""  # o valor não está em nenhuma linha do bloco


def test_column_marker_is_removed_from_header_text_fields(user, tmp_path, monkeypatch):
    header = {
        **SAMPLE_HEADER,
        "laboratory_name": "Laboratório Exemplo | Medicina Diagnóstica",
    }
    llm = FakeLLMClient(header=header, results=sample_llm_results)
    monkeypatch.setattr(tasks, "build_llm_client", lambda: llm)
    pdf = build_pdf(tmp_path / "esparso.pdf", [PAGE_1], identity=SPARSE_HEADER)
    document = create_document(user, pdf)
    tasks.process_exam_document(document.pk)
    document.refresh_from_db()
    assert document.laboratory.name == "Laboratório Exemplo Medicina Diagnóstica"


def test_no_identifier_reaches_llm_or_database(user, sample_pdf, fake_llm):
    document = create_document(user, sample_pdf)
    tasks.process_exam_document(document.pk)
    document.refresh_from_db()

    sent = "\n".join([*fake_llm.header_calls, *fake_llm.result_calls])
    stored = str(document.page_text)
    assert fake_llm.header_calls == []  # o regex leu o cabeçalho: o modelo nem foi chamado
    for blob in (sent, stored):
        assert "123.456.789-09" not in blob
        assert "99999999999" not in blob
    assert "[CPF]" in stored


def test_boilerplate_header_is_stripped_from_blocks(user, sample_pdf, fake_llm):
    document = create_document(user, sample_pdf)
    tasks.process_exam_document(document.pk)
    document.refresh_from_db()
    for text in fake_llm.result_calls:
        assert "Página" not in text
        assert "ASSINATURA DIGITAL" not in text
        assert "Liberado por" not in text
    # Só a tabela do hemograma chega ao LLM; os demais blocos saem por regras.
    assert len(fake_llm.result_calls) == 1
    assert fake_llm.result_calls[0].startswith("HEMOGRAMA")
    # O mesmo vale para o que as regras gravam: nada de cabeçalho/rodapé nas linhas de origem.
    for result in document.results.filter(origin=ResultOrigin.CURRENT):
        for blob in (result.source_line, result.reference_raw):
            assert "Página" not in blob and "Liberado por" not in blob


def test_reprocess_from_stored_text(user, sample_pdf, fake_llm):
    document = create_document(user, sample_pdf)
    tasks.process_exam_document(document.pk)
    first_count = document.results.count()

    tasks.process_exam_document(document.pk, True)
    document.refresh_from_db()
    assert document.results.count() == first_count
    assert document.runs.count() == 2


def test_duplicate_accession_is_rejected_with_code(user, sample_pdf, tmp_path, fake_llm):
    first = create_document(user, sample_pdf)
    tasks.process_exam_document(first.pk)

    other_pdf = build_pdf(tmp_path / "outro.pdf", [PAGE_1, PAGE_1])  # outro hash
    second = create_document(user, other_pdf)
    tasks.process_exam_document(second.pk)
    second.refresh_from_db()

    assert second.status == DocumentStatus.FAILED
    assert second.error_code == ErrorCode.DUPLICATE_DOCUMENT
    assert second.error_params == {"date": "2026-08-25"}
    assert not second.results.exists()


def test_same_accession_for_different_users_is_allowed(
    user, other_user, sample_pdf, tmp_path, fake_llm
):
    first = create_document(user, sample_pdf)
    tasks.process_exam_document(first.pk)
    second = create_document(other_user, build_pdf(tmp_path / "b.pdf", [PAGE_1, PAGE_1]))
    tasks.process_exam_document(second.pk)
    second.refresh_from_db()
    assert second.status != DocumentStatus.FAILED


def test_current_result_replaces_previous_seeded_by_older_report(
    user, sample_pdf, tmp_path, monkeypatch
):
    llm = FakeLLMClient(header=SAMPLE_HEADER, results=sample_llm_results)
    monkeypatch.setattr(tasks, "build_llm_client", lambda: llm)
    document = create_document(user, sample_pdf)
    tasks.process_exam_document(document.pk)
    assert ExamResult.objects.filter(
        user=user,
        origin=ResultOrigin.LAB_PREVIOUS,
        analyte__slug="glicose",
        collected_at__year=2022,
    ).exists()

    # Um laudo antigo (abril/2022) chega depois e traz o resultado "de verdade".
    old_header = {
        **SAMPLE_HEADER,
        "accession_number": "021-00000-001",
        "collected_at": "25/04/2022",
    }
    old_llm = FakeLLMClient(header=old_header)
    monkeypatch.setattr(tasks, "build_llm_client", lambda: old_llm)
    old = create_document(user, old_glucose_pdf(tmp_path / "old.pdf", "25/04/2022 - 07:00:00"))
    tasks.process_exam_document(old.pk)

    on_that_day = ExamResult.objects.filter(
        user=user, analyte__slug="glicose", collected_at__year=2022, collected_at__month=4
    )
    assert on_that_day.count() == 1
    assert on_that_day.get().origin == ResultOrigin.CURRENT


def test_scanned_page_uses_vision_transcription(user, tmp_path, monkeypatch):
    blank = build_pdf(tmp_path / "scan.pdf", [None])  # página sem camada de texto
    transcript = "\n".join(
        [
            "LABORATÓRIO EXEMPLO | Medicina Diagnóstica",
            "Cliente: JOAO TESTE DA SILVA | O.S: 021-77777-000",
            "GLICOSE | 103 mg/dL",
            "Material: soro",
            "Data da coleta: 25/08/2026 - 07:01:51",
            "Valor de Referência: Normal: entre 70 a 99 mg/dL",
            "Liberado por: Dr. Teste",
        ]
    )
    llm = FakeLLMClient(
        header={**SAMPLE_HEADER, "accession_number": "021-77777-000"},
        results=sample_llm_results,
        vision_text=transcript,
    )
    monkeypatch.setattr(tasks, "build_llm_client", lambda: llm)
    document = create_document(user, blank)
    tasks.process_exam_document(document.pk)
    document.refresh_from_db()

    assert llm.vision_calls == 1
    assert document.extraction_method == "vision"
    glucose = by_name(document, "GLICOSE")
    assert WarningCode.PAGE_READ_BY_VISION in warning_codes(glucose)
    assert glucose.value_numeric == Decimal("103")


def test_document_without_results_fails_with_code(user, tmp_path, monkeypatch):
    llm = FakeLLMClient(header=SAMPLE_HEADER)  # LLM sem resultado nenhum
    monkeypatch.setattr(tasks, "build_llm_client", lambda: llm)
    # Só blocos qualitativos: as regras não leem nada e o LLM também não.
    body = [*qualitative_block("NITRITO", "Negativo"), *qualitative_block("COR", "Amarela")]
    document = create_document(user, build_pdf(tmp_path / "vazio.pdf", [body]))
    tasks.process_exam_document(document.pk)
    document.refresh_from_db()
    assert document.status == DocumentStatus.FAILED
    assert document.error_code == ErrorCode.NO_RESULTS_FOUND
    assert document.upload_path == ""


def test_missing_sex_keeps_value_and_reports_reason(user, sample_pdf, fake_llm):
    user.exam_profile.sex = ""
    user.exam_profile.save()
    document = create_document(user, sample_pdf)
    tasks.process_exam_document(document.pk)
    creatinine = by_name(document, "CREATININA")
    assert creatinine.ref_low is None
    assert creatinine.flag == "unknown"
    assert WarningCode.SEX_REQUIRED_FOR_REFERENCE in warning_codes(creatinine)
    # informativo: não manda o resultado para revisão por si só
    assert creatinine.review_status == ReviewStatus.AUTO


def test_patient_mismatch_is_flagged_on_document(user, sample_pdf, fake_llm):
    user.first_name, user.last_name = "Carlos", "Outro"
    user.save()
    document = create_document(user, sample_pdf)
    tasks.process_exam_document(document.pk)
    document.refresh_from_db()
    assert {w["code"] for w in document.warnings} == {WarningCode.PATIENT_MISMATCH}
    assert document.status == DocumentStatus.NEEDS_REVIEW


def test_unreadable_pdf_fails_with_code(user, tmp_path, fake_llm):
    broken = tmp_path / "quebrado.pdf"
    broken.write_bytes(b"%PDF-1.4 isto nao e um pdf valido")
    document = create_document(user, broken)
    tasks.process_exam_document(document.pk)
    document.refresh_from_db()
    assert document.status == DocumentStatus.FAILED
    assert document.error_code in (ErrorCode.PDF_UNREADABLE, ErrorCode.EXTRACTION_FAILED)
    assert not Path(document.upload_path or "x").exists()


def test_catalog_has_no_duplicate_alias_conflicts():
    assert Analyte.objects.filter(slug="glicose").exists()
    keys = [a for a in re.findall(r"\w+", "glicose")]
    assert keys


def test_late_evening_collection_is_deduplicated_in_local_timezone(
    user, sample_pdf, tmp_path, monkeypatch
):
    # 25/04/2022 22:30 em São Paulo = 26/04 01:30 UTC: o banco guarda UTC.
    old_llm = FakeLLMClient(
        header={**SAMPLE_HEADER, "accession_number": "021-00000-001"},
    )
    monkeypatch.setattr(tasks, "build_llm_client", lambda: old_llm)
    old = create_document(user, old_glucose_pdf(tmp_path / "old.pdf", "25/04/2022 - 22:30:00"))
    tasks.process_exam_document(old.pk)
    stored = ExamResult.objects.get(user=user, analyte__slug="glicose", origin=ResultOrigin.CURRENT)
    assert stored.collected_at.date().isoformat() == "2022-04-26"  # UTC
    assert timezone.localtime(stored.collected_at).date().isoformat() == "2022-04-25"

    # Um laudo novo traz "Anteriores: 25/04/2022 - 92": já existe esse dia, não duplica.
    llm = FakeLLMClient(header=SAMPLE_HEADER, results=sample_llm_results)
    monkeypatch.setattr(tasks, "build_llm_client", lambda: llm)
    newer = create_document(user, sample_pdf)
    tasks.process_exam_document(newer.pk)

    same_day = [
        r
        for r in ExamResult.objects.filter(user=user, analyte__slug="glicose")
        if timezone.localtime(r.collected_at).date().isoformat() == "2022-04-25"
    ]
    assert len(same_day) == 1
    assert same_day[0].origin == ResultOrigin.CURRENT


def test_late_evening_current_replaces_seeded_previous(user, sample_pdf, tmp_path, monkeypatch):
    llm = FakeLLMClient(header=SAMPLE_HEADER, results=sample_llm_results)
    monkeypatch.setattr(tasks, "build_llm_client", lambda: llm)
    tasks.process_exam_document(create_document(user, sample_pdf).pk)

    old_llm = FakeLLMClient(
        header={**SAMPLE_HEADER, "accession_number": "021-00000-001"},
    )
    monkeypatch.setattr(tasks, "build_llm_client", lambda: old_llm)
    old_pdf = old_glucose_pdf(tmp_path / "o.pdf", "25/04/2022 - 22:30:00")
    tasks.process_exam_document(create_document(user, old_pdf).pk)

    same_day = [
        r
        for r in ExamResult.objects.filter(user=user, analyte__slug="glicose")
        if timezone.localtime(r.collected_at).date().isoformat() == "2022-04-25"
    ]
    assert [r.origin for r in same_day] == [ResultOrigin.CURRENT]


def test_duplicate_accession_without_laboratory_is_rejected(
    user, sample_pdf, tmp_path, monkeypatch
):
    # NULL nunca colide na constraint única do Postgres: a checagem é explícita.
    header = {**SAMPLE_HEADER, "laboratory_name": ""}
    llm = FakeLLMClient(header=header, results=sample_llm_results)
    monkeypatch.setattr(tasks, "build_llm_client", lambda: llm)
    no_site = build_pdf(tmp_path / "a.pdf", [PAGE_1], identity=HEADER_WITHOUT_SITE)
    first = create_document(user, no_site)
    tasks.process_exam_document(first.pk)
    first.refresh_from_db()
    assert first.laboratory is None
    assert first.status != DocumentStatus.FAILED

    other = build_pdf(tmp_path / "b.pdf", [PAGE_1, PAGE_1], identity=HEADER_WITHOUT_SITE)
    second = create_document(user, other)
    tasks.process_exam_document(second.pk)
    second.refresh_from_db()
    assert second.status == DocumentStatus.FAILED
    assert second.error_code == ErrorCode.DUPLICATE_DOCUMENT
    assert second.error_params == {"date": "2026-08-25"}


def test_broadcast_failure_does_not_fail_extraction(user, sample_pdf, fake_llm, monkeypatch):
    def broken(*args, **kwargs):
        raise ConnectionError("redis fora do ar")

    monkeypatch.setattr(tasks, "broadcast_document_event", broken)
    document = create_document(user, sample_pdf)
    stored_path = Path(document.upload_path)

    tasks.process_exam_document(document.pk)
    document.refresh_from_db()

    assert document.status != DocumentStatus.FAILED
    assert document.results.exists()
    assert document.runs.get().status == "success"
    assert not stored_path.exists()


def test_failure_before_extraction_still_deletes_pdf_and_marks_failed(
    user, sample_pdf, fake_llm, monkeypatch
):
    def boom(*args, **kwargs):
        raise RuntimeError("banco indisponível")

    monkeypatch.setattr(ExtractionRun.objects, "create", boom)
    document = create_document(user, sample_pdf)
    stored_path = Path(document.upload_path)

    tasks.process_exam_document(document.pk)
    document.refresh_from_db()

    assert document.status == DocumentStatus.FAILED
    assert document.error_code == ErrorCode.EXTRACTION_FAILED
    assert document.upload_path == ""
    assert not stored_path.exists()
