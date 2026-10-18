"""`manage.py import_reports`: importação em lote offline (PDFs sintéticos, LLM falso)."""

import csv
from io import StringIO
from pathlib import Path

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.exams import tasks
from apps.exams.extraction.fakes import FakeLLMClient
from apps.exams.models import DocumentStatus, ExamDocument, ExamResult

from .conftest import (
    PAGE_1,
    PAGE_2,
    SAMPLE_HEADER,
    build_pdf,
    header_with_accession,
    sample_llm_results,
)

pytestmark = pytest.mark.django_db


def make_pdf(folder: Path, name: str, accession: str, pages=(PAGE_2,)) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    return build_pdf(folder / name, list(pages), identity=header_with_accession(accession))


def run(*args: str) -> str:
    out = StringIO()
    call_command("import_reports", *args, stdout=out)
    return out.getvalue()


@pytest.fixture(autouse=True)
def fake_llm(monkeypatch):
    llm = FakeLLMClient(header=SAMPLE_HEADER, results=sample_llm_results)
    monkeypatch.setattr(tasks, "build_llm_client", lambda: llm)
    return llm


def test_imports_every_pdf_in_a_folder_and_leaves_originals_untouched(user, tmp_path, settings):
    first = make_pdf(tmp_path / "laudos", "a.pdf", "021-00001-001")
    second = make_pdf(tmp_path / "laudos", "b.pdf", "021-00002-002")
    before = {p: p.read_bytes() for p in (first, second)}

    output = run(str(tmp_path / "laudos"), "--email", user.email)

    assert ExamDocument.objects.filter(user=user).count() == 2
    assert {d.original_filename for d in ExamDocument.objects.all()} == {"a.pdf", "b.pdf"}
    assert all(d.upload_path == "" for d in ExamDocument.objects.all())
    assert ExamResult.objects.filter(user=user).exists()
    # originais intactos; só a cópia temporária é apagada
    assert {p: p.read_bytes() for p in (first, second)} == before
    assert not list(Path(settings.EXAMS_UPLOAD_DIR).glob("*"))
    assert "2 arquivo(s)" in output and "[2/2]" in output


def test_rerun_skips_already_imported_files(user, tmp_path):
    make_pdf(tmp_path, "a.pdf", "021-00001-001")
    run(str(tmp_path), "--email", user.email)
    count = ExamResult.objects.count()

    output = run(str(tmp_path), "--email", user.email)

    assert ExamDocument.objects.count() == 1
    assert ExamResult.objects.count() == count
    assert "pulado (já importado)" in output
    assert "pulados (duplicados): 1" in output


def test_same_order_number_in_another_file_is_skipped_and_leaves_no_failed_document(user, tmp_path):
    make_pdf(tmp_path, "a.pdf", "021-00001-001")
    # outro arquivo (conteúdo diferente) do MESMO pedido: o pipeline o rejeita como duplicado
    make_pdf(tmp_path, "b.pdf", "021-00001-001", pages=(PAGE_2, PAGE_2))

    output = run(str(tmp_path), "--email", user.email)

    assert ExamDocument.objects.count() == 1
    assert ExamDocument.objects.get().status != DocumentStatus.FAILED
    assert "pulados (duplicados): 1" in output


def test_dry_run_writes_nothing(user, tmp_path, settings):
    make_pdf(tmp_path, "a.pdf", "021-00001-001")

    output = run(str(tmp_path), "--email", user.email, "--dry-run")

    assert ExamDocument.objects.count() == 0
    assert not list(Path(settings.EXAMS_UPLOAD_DIR).glob("*"))
    assert "seria importado" in output and "simulação" in output


def test_non_pdf_and_corrupt_files_do_not_stop_the_batch(user, tmp_path):
    folder = tmp_path / "mix"
    make_pdf(folder, "bom.pdf", "021-00001-001")
    (folder / "notas.txt").write_text("nao e pdf")
    (folder / "falso.pdf").write_bytes(b"isto nao comeca com a assinatura de pdf")
    (folder / "quebrado.pdf").write_bytes(b"%PDF-1.4 conteudo quebrado")

    output = run(str(folder), "--email", user.email)

    assert "notas.txt" not in output  # só *.pdf é considerado numa pasta
    assert "falso.pdf: pulado (arquivo inválido) — file_not_pdf" in output
    assert "bom.pdf" in output
    docs = {d.original_filename: d.status for d in ExamDocument.objects.all()}
    assert docs["bom.pdf"] != DocumentStatus.FAILED
    assert docs["quebrado.pdf"] == DocumentStatus.FAILED  # a falha fica visível no app
    assert "falso.pdf" not in docs


def test_recursive_flag_enters_subfolders(user, tmp_path):
    make_pdf(tmp_path, "raiz.pdf", "021-00001-001")
    make_pdf(tmp_path / "2019", "antigo.pdf", "021-00002-002")

    run(str(tmp_path), "--email", user.email)
    assert ExamDocument.objects.count() == 1

    run(str(tmp_path), "--email", user.email, "--recursive")
    assert {d.original_filename for d in ExamDocument.objects.all()} == {"raiz.pdf", "antigo.pdf"}


def test_no_llm_never_calls_the_model_and_flags_what_needs_it(user, tmp_path, fake_llm, settings):
    # PAGE_1 tem o hemograma (tabela que as regras rejeitam) e blocos lidos por regras.
    make_pdf(tmp_path, "a.pdf", "021-00001-001", pages=(PAGE_1,))
    output = run(str(tmp_path), "--email", user.email, "--no-llm")

    assert fake_llm.result_calls == []
    assert fake_llm.header_calls == []
    document = ExamDocument.objects.get()
    assert document.status == DocumentStatus.NEEDS_REVIEW
    codes = {w["code"] for w in document.warnings}
    assert "block_unreadable" in codes
    assert "llm_budget_exceeded" not in codes  # desligado de propósito não é "tempo esgotado"
    assert document.results.filter(name_raw="GLICOSE").exists()  # o que as regras leem entra
    assert "0 chamadas ao modelo" in output
    assert "só regras, sem modelo" in output
    assert settings.EXTRACTION_USE_LLM is True  # `--no-llm` vale só durante o comando


def test_report_csv_has_one_row_per_file_without_exam_values(user, tmp_path):
    make_pdf(tmp_path, "a.pdf", "021-00001-001")
    csv_path = tmp_path / "resumo.csv"

    run(str(tmp_path), "--email", user.email, "--report", str(csv_path))

    rows = list(csv.DictReader(csv_path.open(encoding="utf-8")))
    assert [r["arquivo"] for r in rows] == ["a.pdf"]
    assert rows[0]["status"] in {DocumentStatus.COMPLETED, DocumentStatus.NEEDS_REVIEW}
    assert int(rows[0]["resultados"]) > 0
    text = csv_path.read_text(encoding="utf-8")
    assert "GLICOSE" not in text and "103" not in text  # contagens, nunca valores de exame


def test_limit_processes_only_the_first_files(user, tmp_path):
    for i in range(3):
        make_pdf(tmp_path, f"{i}.pdf", f"021-0000{i}-00{i}")

    run(str(tmp_path), "--email", user.email, "--limit", "2")

    assert ExamDocument.objects.count() == 2


def test_documents_belong_to_the_chosen_user_only(user, other_user, tmp_path):
    make_pdf(tmp_path, "a.pdf", "021-00001-001")

    run(str(tmp_path), "--email", other_user.email)

    assert ExamDocument.objects.filter(user=other_user).count() == 1
    assert ExamDocument.objects.filter(user=user).count() == 0


def test_user_resolution_errors(user, other_user, tmp_path):
    make_pdf(tmp_path, "a.pdf", "021-00001-001")
    with pytest.raises(CommandError, match="Informe --email"):
        run(str(tmp_path))  # há dois usuários e nenhum foi escolhido
    with pytest.raises(CommandError, match="não encontrado"):
        run(str(tmp_path), "--email", "ninguem@example.com")


def test_single_user_does_not_need_email(user, tmp_path):
    make_pdf(tmp_path, "a.pdf", "021-00001-001")
    run(str(tmp_path))
    assert ExamDocument.objects.filter(user=user).count() == 1


def test_missing_path_and_empty_folder_are_errors(user, tmp_path):
    with pytest.raises(CommandError, match="não encontrado"):
        run(str(tmp_path / "nao-existe"), "--email", user.email)
    (tmp_path / "vazia").mkdir()
    with pytest.raises(CommandError, match="Nenhum PDF"):
        run(str(tmp_path / "vazia"), "--email", user.email)


def test_accepts_individual_files_and_deduplicates_repeated_paths(user, tmp_path):
    pdf = make_pdf(tmp_path, "a.pdf", "021-00001-001")
    run(str(pdf), str(tmp_path), "--email", user.email)  # o mesmo arquivo por dois caminhos
    assert ExamDocument.objects.count() == 1


# --- --laboratory-map: nome que só está no logotipo -------------------------------------------


@pytest.fixture
def nothing_detected(monkeypatch):
    """O texto dos PDFs sintéticos traz um site; aqui o laboratório não é detectado."""
    monkeypatch.setattr(
        "apps.exams.extraction.pipeline.laboratory_from_text", lambda *args, **kwargs: ""
    )


def write_map(tmp_path: Path, content: str) -> str:
    path = tmp_path / "laboratories.json"
    path.write_text(content, encoding="utf-8")
    return str(path)


def test_laboratory_map_fills_documents_without_a_detected_laboratory(
    user, tmp_path, nothing_detected
):
    make_pdf(tmp_path / "laudos", "a.pdf", "021-00001-001")
    make_pdf(tmp_path / "laudos", "b.pdf", "021-00002-002")
    # o primeiro padrão que casa vence; maiúsculas não importam
    mapping = write_map(tmp_path, '{"A*.PDF": "Lab do Logotipo", "*.pdf": "Outro Lab"}')

    output = run(str(tmp_path / "laudos"), "--email", user.email, "--laboratory-map", mapping)

    names = {d.original_filename: d.laboratory.name for d in ExamDocument.objects.all()}
    assert names == {"a.pdf": "Lab do Logotipo", "b.pdf": "Outro Lab"}
    assert "laboratório pelo mapa: 2" in output


def test_laboratory_map_never_replaces_a_detected_name(user, tmp_path, monkeypatch):
    monkeypatch.setattr(
        "apps.exams.extraction.pipeline.laboratory_from_text", lambda *args, **kwargs: "Detectado"
    )
    make_pdf(tmp_path / "laudos", "a.pdf", "021-00001-001")
    mapping = write_map(tmp_path, '{"*.pdf": "Do Mapa"}')

    output = run(str(tmp_path / "laudos"), "--email", user.email, "--laboratory-map", mapping)

    assert ExamDocument.objects.get().laboratory.name == "Detectado"
    assert "laboratório pelo mapa: 0" in output


def test_laboratory_map_is_skipped_for_files_no_pattern_matches(user, tmp_path, nothing_detected):
    make_pdf(tmp_path / "laudos", "a.pdf", "021-00001-001")
    mapping = write_map(tmp_path, '{"outro-*.pdf": "Não casa"}')

    run(str(tmp_path / "laudos"), "--email", user.email, "--laboratory-map", mapping)

    assert ExamDocument.objects.get().laboratory is None


@pytest.mark.parametrize(
    "content",
    ["não é json", "[]", "{}", '{"*.pdf": ""}', '{"*.pdf": 3}'],
)
def test_invalid_laboratory_map_is_rejected_before_importing_anything(user, tmp_path, content):
    make_pdf(tmp_path / "laudos", "a.pdf", "021-00001-001")

    with pytest.raises(CommandError, match="laboratórios|Entrada inválida"):
        run(
            str(tmp_path / "laudos"),
            "--email",
            user.email,
            "--laboratory-map",
            write_map(tmp_path, content),
        )

    assert not ExamDocument.objects.exists()


def test_missing_laboratory_map_file_is_an_error(user, tmp_path):
    make_pdf(tmp_path / "laudos", "a.pdf", "021-00001-001")

    with pytest.raises(CommandError, match="Não consegui ler"):
        run(
            str(tmp_path / "laudos"),
            "--email",
            user.email,
            "--laboratory-map",
            str(tmp_path / "nao-existe.json"),
        )
