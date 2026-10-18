"""`manage.py export_reports`: PDFs -> JSON revisável, sem nada no banco (dados sintéticos)."""

import json
import os
import stat
import subprocess
from pathlib import Path

import pytest
from django.conf import settings as django_settings
from django.core.management.base import CommandError

from apps.exams import tasks
from apps.exams.extraction.fakes import FakeLLMClient
from apps.exams.models import AliasSource, Analyte, AnalyteAlias, ExamDocument

from .conftest import PAGE_2, SAMPLE_HEADER, build_pdf, header_with_accession, sample_llm_results
from .report_set import build_set, db_counts, export, run_command

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def fake_llm(monkeypatch):
    llm = FakeLLMClient(header=SAMPLE_HEADER, results=sample_llm_results)
    monkeypatch.setattr(tasks, "build_llm_client", lambda: llm)
    return llm


@pytest.fixture
def pdfs(tmp_path):
    return build_set(tmp_path / "laudos")


@pytest.fixture
def out(tmp_path) -> Path:
    return tmp_path / "saida" / "old_reports.json"


@pytest.fixture(autouse=True)
def out_dir(tmp_path):
    (tmp_path / "saida").mkdir()


def test_json_has_the_documented_envelope_and_document_shape(user, pdfs, out):
    data = export(out.parent.parent / "laudos", out)

    assert data["format"] == "myexams-reports/1"
    assert data["contains_health_data"] is True
    assert data["generated_at"]
    assert "dados pessoais de saúde" in data["note"] and "keep" in data["note"]
    assert [d["source_file"] for d in data["documents"]] == [
        "a_completo.pdf",
        "b_simples.pdf",
        "c_imagem.pdf",
    ]
    document = data["documents"][0]
    assert set(document) >= {
        "source_file",
        "sha256",
        "status",
        "error_code",
        "error_params",
        "warnings",
        "laboratory",
        "accession_number",
        "ordering_physician",
        "patient_name_on_doc",
        "patient_birth_date",
        "collected_on",
        "issued_at",
        "page_count",
        "extraction_method",
        "keep",
        "results",
    }
    assert document["accession_number"] == "021-00001-001"
    assert len(document["sha256"]) == 64
    assert "page_text" not in document  # só com --include-text


def test_result_rows_carry_raw_normalized_and_a_human_summary(user, pdfs, out):
    data = export(out.parent.parent / "laudos", out)
    results = data["documents"][0]["results"]
    creatinine = next(
        r for r in results if r["analyte"] == "creatinina" and r["origin"] == "current"
    )

    assert set(creatinine) >= {
        "summary",
        "keep",
        "reviewed",
        "analyte",
        "name_raw",
        "section_raw",
        "origin",
        "page",
        "position",
        "value_raw",
        "unit_raw",
        "reference_raw",
        "flag_printed",
        "comparator",
        "value_numeric",
        "value_text",
        "unit",
        "value_canonical",
        "ref_kind",
        "ref_low",
        "ref_high",
        "ref_low_inclusive",
        "ref_high_inclusive",
        "ref_bands",
        "flag",
        "interpretation",
        "collected_at",
        "review_status",
        "warnings",
        "source_line",
        "fingerprint",
    }
    assert creatinine["value_raw"] == "1,20"
    assert creatinine["value_numeric"] == "1.2"  # Decimal exato, em texto
    assert creatinine["flag"] == "normal"
    assert creatinine["keep"] is True and creatinine["reviewed"] is False
    assert creatinine["summary"].startswith("Creatinina 1,20 mg/dL (0,7 a 1,3)")
    assert creatinine["summary"].endswith("normal")
    assert any(r["origin"] == "lab_previous" for r in results)
    previous = next(r for r in results if r["origin"] == "lab_previous")
    assert previous["summary"].startswith("[anterior ")


def test_failed_document_is_listed_with_status_and_no_results(user, pdfs, out):
    data = export(out.parent.parent / "laudos", out)
    failed = next(d for d in data["documents"] if d["source_file"] == "c_imagem.pdf")

    assert failed["status"] == "failed"
    assert failed["error_code"] == "no_results_found"
    assert failed["results"] == []
    assert len(failed["sha256"]) == 64


def test_invalid_files_are_listed_as_failed_instead_of_aborting(user, tmp_path, out):
    folder = tmp_path / "mix"
    folder.mkdir()
    (folder / "falso.pdf").write_bytes(b"isto nao comeca com a assinatura de pdf")
    data = export(folder, out)

    [document] = data["documents"]
    assert document["status"] == "failed" and document["error_code"] == "file_not_pdf"
    assert document["results"] == []


def test_export_leaves_the_database_untouched(user, other_user, pdfs, out, settings):
    before = db_counts()

    export(out.parent.parent / "laudos", out, "--email", user.email)

    assert db_counts() == before
    assert ExamDocument.objects.count() == 0
    assert not list(Path(settings.EXAMS_UPLOAD_DIR).glob("*"))  # nenhuma cópia temporária


def test_export_does_not_learn_or_persist_aliases_nor_lab(user, pdfs, out):
    AnalyteAlias.objects.create(
        analyte=Analyte.objects.get(slug="glicose"),
        user=user,
        alias_normalized="meu apelido",
        source=AliasSource.USER,
    )
    before = AnalyteAlias.objects.count()
    export(out.parent.parent / "laudos", out)
    assert AnalyteAlias.objects.count() == before


def test_originals_are_untouched(user, pdfs, out):
    before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in pdfs.values()}
    export(out.parent.parent / "laudos", out)
    assert {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in pdfs.values()} == before
    assert all(p.exists() for p in pdfs.values())


def test_export_never_calls_the_model_and_does_not_leak_settings(user, pdfs, out, fake_llm):
    budget = django_settings.EXTRACTION_LLM_BUDGET_SECONDS

    data = export(out.parent.parent / "laudos", out)

    assert fake_llm.result_calls == [] and fake_llm.header_calls == []
    assert django_settings.EXTRACTION_LLM_BUDGET_SECONDS == budget
    # o hemograma só o modelo lê: sem modelo, o laudo fica para revisão com o aviso
    first = data["documents"][0]
    assert first["status"] == "needs_review"
    assert "llm_budget_exceeded" in {w["code"] for w in first["warnings"]}


def test_ordering_is_stable_across_exports(user, pdfs, out, tmp_path):
    first = export(out.parent.parent / "laudos", out)
    second = export(out.parent.parent / "laudos", tmp_path / "saida" / "outro.json")

    assert first["documents"] == second["documents"]
    for document in first["documents"]:
        keys = [(r["page"], r["position"]) for r in document["results"]]
        assert keys == sorted(keys)


def test_output_is_private_and_overwrite_needs_force(user, pdfs, out):
    folder = out.parent.parent / "laudos"
    export(folder, out)
    assert stat.S_IMODE(os.stat(out).st_mode) == 0o600

    out.write_text("conteúdo anterior")
    with pytest.raises(CommandError, match="--force"):
        run_command("export_reports", str(folder), "-o", str(out))
    assert out.read_text() == "conteúdo anterior"

    os.chmod(out, 0o644)
    export(folder, out, "--force")
    assert json.loads(out.read_text(encoding="utf-8"))["format"] == "myexams-reports/1"
    assert stat.S_IMODE(os.stat(out).st_mode) == 0o600


def test_missing_output_directory_is_a_clear_error(user, pdfs, tmp_path):
    with pytest.raises(CommandError, match="pasta"):
        run_command(
            "export_reports", str(tmp_path / "laudos"), "-o", str(tmp_path / "nao" / "x.json")
        )


def test_stdout_has_only_names_and_counts_never_values(user, pdfs, out):
    output = run_command("export_reports", str(out.parent.parent / "laudos"), "-o", str(out))

    assert "a_completo.pdf" in output and "[3/3]" in output
    for forbidden in ("GLICOSE", "Glicose", "CREATININA", "1,20", "103", "JOAO", "021-00001-001"):
        assert forbidden not in output


def test_include_text_embeds_the_redacted_page_text(user, pdfs, out):
    data = export(out.parent.parent / "laudos", out, "--include-text")

    page_text = data["documents"][0]["page_text"]
    assert [p["number"] for p in page_text] == [1, 2]
    assert "123.456.789-09" not in json.dumps(page_text)  # CPF já redigido


def test_same_content_in_two_files_is_exported_once(user, pdfs, out, tmp_path):
    copy = out.parent.parent / "laudos" / "a_copia.pdf"
    copy.write_bytes(pdfs["a"].read_bytes())

    data = export(out.parent.parent / "laudos", out)

    assert [d["source_file"] for d in data["documents"]].count("a_completo.pdf") == 1
    assert "a_copia.pdf" not in [d["source_file"] for d in data["documents"]]


def test_limit_and_recursive(user, pdfs, out):
    folder = out.parent.parent / "laudos"
    assert len(export(folder, out, "--limit", "1")["documents"]) == 1

    sub = folder / "2019"
    sub.mkdir()
    build_pdf(sub / "d.pdf", [PAGE_2], identity=header_with_accession("021-00004-004"))
    flat = [d["source_file"] for d in export(folder, out, "--force")["documents"]]
    deep = [d["source_file"] for d in export(folder, out, "--force", "--recursive")["documents"]]
    assert "d.pdf" not in flat and "d.pdf" in deep


def test_profile_of_the_chosen_user_drives_the_reference_band(user, other_user, pdfs, out):
    folder = out.parent.parent / "laudos"

    def creatinine_high(email: str) -> str:
        data = export(folder, out, "--force", "--email", email)
        rows = data["documents"][1]["results"]  # b_simples.pdf
        return next(r for r in rows if r["analyte"] == "creatinina")["ref_high"]

    assert creatinine_high(user.email) == "1.3"  # homem
    assert creatinine_high(other_user.email) == "1.1"  # mulher


def test_user_resolution_errors(user, other_user, pdfs, out):
    folder = str(out.parent.parent / "laudos")
    with pytest.raises(CommandError, match="Informe --email"):
        run_command("export_reports", folder, "-o", str(out))
    with pytest.raises(CommandError, match="não encontrado"):
        run_command("export_reports", folder, "-o", str(out), "--email", "x@example.com")
    with pytest.raises(CommandError, match="Nenhum PDF"):
        (out.parent / "vazia").mkdir()
        run_command(
            "export_reports", str(out.parent / "vazia"), "-o", str(out), "--email", user.email
        )


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True)


def test_warns_when_output_is_in_a_repo_and_not_git_ignored(user, pdfs, tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    folder = tmp_path / "laudos"

    warned = run_command("export_reports", str(folder), "-o", str(repo / "visivel.json"))
    assert "git" in warned and "ignorad" in warned

    (repo / ".gitignore").write_text("privado.json\n")
    quiet = run_command("export_reports", str(folder), "-o", str(repo / "privado.json"))
    assert "ignorad" not in quiet
