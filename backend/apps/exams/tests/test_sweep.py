import os
import time
from datetime import timedelta
from io import StringIO

import pytest
from django.core.management import call_command
from django.utils import timezone

from apps.exams.codes import ErrorCode
from apps.exams.models import DocumentStatus, ExamDocument, ExtractionRun, RunStatus
from apps.exams.storage import upload_dir
from apps.exams.tasks import sweep_orphans

from .conftest import create_document

pytestmark = pytest.mark.django_db

HOURS = 3600


def age(path, hours):
    stamp = time.time() - hours * HOURS
    os.utime(path, (stamp, stamp))


def age_document(document, hours):
    ExamDocument.objects.filter(pk=document.pk).update(
        updated_at=timezone.now() - timedelta(hours=hours)
    )


def test_old_files_are_deleted_and_recent_are_kept(settings):
    settings.EXAMS_ORPHAN_HOURS = 3
    old = upload_dir() / "velho.pdf"
    recent = upload_dir() / "novo.pdf"
    old.write_bytes(b"%PDF-")
    recent.write_bytes(b"%PDF-")
    age(old, 5)
    age(recent, 1)

    counts = sweep_orphans()

    assert not old.exists()
    assert recent.exists()
    assert counts["files_deleted"] == 1


def test_directories_and_symlinks_are_left_alone():
    target = upload_dir().parent / "fora.txt"
    target.write_text("não apague")
    link = upload_dir() / "link.pdf"
    link.symlink_to(target)
    subdir = upload_dir() / "sub"
    subdir.mkdir()
    age(target, 10)
    os.utime(subdir, (time.time() - 10 * HOURS,) * 2)

    sweep_orphans()

    assert target.exists()
    assert subdir.exists()


def test_explicit_hours_argument_overrides_setting(settings):
    settings.EXAMS_ORPHAN_HOURS = 100
    path = upload_dir() / "a.pdf"
    path.write_bytes(b"%PDF-")
    age(path, 2)
    assert sweep_orphans()["files_deleted"] == 0
    assert sweep_orphans(1)["files_deleted"] == 1
    assert not path.exists()


def test_path_to_missing_file_is_cleared(user, sample_pdf):
    document = create_document(user, sample_pdf)
    os.unlink(document.upload_path)

    counts = sweep_orphans()

    document.refresh_from_db()
    assert document.upload_path == ""
    assert document.status == DocumentStatus.UPLOADED  # recente: não é falha
    assert counts["paths_cleared"] == 1


def test_path_to_existing_file_is_kept(user, sample_pdf):
    document = create_document(user, sample_pdf)
    sweep_orphans()
    document.refresh_from_db()
    assert document.upload_path


@pytest.mark.parametrize("status", [DocumentStatus.EXTRACTING, DocumentStatus.UPLOADED])
def test_stuck_document_is_failed_and_file_deleted(user, sample_pdf, status):
    document = create_document(user, sample_pdf)
    stored = document.upload_path
    ExamDocument.objects.filter(pk=document.pk).update(status=status)
    run = ExtractionRun.objects.create(document=document, status=RunStatus.RUNNING)
    age_document(document, 5)

    counts = sweep_orphans()

    document.refresh_from_db()
    run.refresh_from_db()
    assert document.status == DocumentStatus.FAILED
    assert document.error_code == ErrorCode.EXTRACTION_FAILED
    assert document.upload_path == ""
    assert not os.path.exists(stored)
    assert run.status == RunStatus.FAILED
    assert counts["documents_failed"] == 1


def test_recent_stuck_document_is_untouched(user, sample_pdf):
    document = create_document(user, sample_pdf)
    ExamDocument.objects.filter(pk=document.pk).update(status=DocumentStatus.EXTRACTING)
    age_document(document, 1)

    counts = sweep_orphans()

    document.refresh_from_db()
    assert document.status == DocumentStatus.EXTRACTING
    assert os.path.exists(document.upload_path)
    assert counts == {"documents_failed": 0, "files_deleted": 0, "paths_cleared": 0}


def test_finished_documents_are_never_failed(user, sample_pdf):
    document = create_document(user, sample_pdf)
    ExamDocument.objects.filter(pk=document.pk).update(status=DocumentStatus.COMPLETED)
    age_document(document, 50)
    sweep_orphans()
    document.refresh_from_db()
    assert document.status == DocumentStatus.COMPLETED


def test_counts_contain_no_filenames(user, sample_pdf):
    create_document(user, sample_pdf)
    counts = sweep_orphans()
    assert set(counts) == {"documents_failed", "files_deleted", "paths_cleared"}
    assert all(isinstance(value, int) for value in counts.values())


def test_management_command(user, sample_pdf):
    path = upload_dir() / "velho.pdf"
    path.write_bytes(b"%PDF-")
    age(path, 4)
    out = StringIO()

    call_command("sweep_uploads", "--hours", "1", stdout=out)

    assert not path.exists()
    assert "1 arquivos apagados" in out.getvalue()
