import logging
import os
from datetime import timedelta
from pathlib import Path

from celery import shared_task
from django.conf import settings
from django.utils import timezone

from .codes import ErrorCode
from .events import broadcast_document_event
from .extraction.errors import ExtractionError
from .extraction.llm import build_llm_client
from .extraction.pipeline import process_document
from .extraction.schema import PROMPT_VERSION
from .extraction.telemetry import LLMTelemetry
from .models import DocumentStatus, ExamDocument, ExtractionRun, RunStatus
from .storage import delete_upload, upload_dir

logger = logging.getLogger(__name__)


def _broadcast(user_id: int, document_id: int, status: str) -> None:
    """Notifica o frontend sem nunca derrubar a extração (Redis fora do ar, por exemplo)."""
    try:
        broadcast_document_event(user_id, document_id, status)
    except Exception as exc:
        # Sem dados do laudo: só o id e o tipo do erro.
        logger.warning(
            "Não foi possível notificar o frontend sobre o laudo %s (%s)",
            document_id,
            type(exc).__name__,
        )


def _progress_reporter(document: ExamDocument):
    """Callback de progresso: grava `progress_done/total` e avisa o frontend.

    Nunca derruba a extração: falha de banco ou de broadcast só é registrada.
    """

    def report(done: int, total: int) -> None:
        document.progress_done, document.progress_total = done, total
        try:
            ExamDocument.objects.filter(pk=document.pk).update(
                progress_done=done, progress_total=total
            )
        except Exception as exc:
            logger.warning(
                "Não foi possível gravar o progresso do laudo %s (%s)",
                document.pk,
                type(exc).__name__,
            )
        _broadcast(document.user_id, document.pk, DocumentStatus.EXTRACTING)

    return report


@shared_task
def process_exam_document(document_id: int, reprocess: bool = False) -> None:
    """Extrai os resultados de um laudo e descarta o PDF ao final.

    Idempotente: reexecutar substitui os resultados. Falhas viram código +
    params no laudo (o frontend traduz); nenhum valor de exame é logado.
    """
    document = ExamDocument.objects.select_related("user").get(pk=document_id)
    user_id = document.user_id
    run: ExtractionRun | None = None
    # Das chamadas ao modelo medidas até aqui: sobrevive à falha, para o laudo que morreu
    # num timeout mostrar onde o tempo foi.
    telemetry = LLMTelemetry()

    # Tudo que pode falhar fica dentro do try: qualquer erro ainda chega ao `finally`,
    # que apaga o PDF e marca o laudo como falho.
    try:
        pdf_path = None if reprocess or not document.upload_path else Path(document.upload_path)
        run = ExtractionRun.objects.create(
            document=document,
            status=RunStatus.RUNNING,
            started_at=timezone.now(),
            prompt_version=PROMPT_VERSION,
        )
        document.status = DocumentStatus.EXTRACTING
        document.error_code = ""
        document.error_params = {}
        document.progress_done = document.progress_total = 0
        document.save(
            update_fields=[
                "status",
                "error_code",
                "error_params",
                "progress_done",
                "progress_total",
                "updated_at",
            ]
        )
        _broadcast(user_id, document.pk, DocumentStatus.EXTRACTING)

        llm = build_llm_client()
        run.model, run.vision_model = llm.model, llm.vision_model
        outcome = process_document(document, llm, pdf_path, _progress_reporter(document), telemetry)
        run.status = RunStatus.SUCCESS
        run.stats = outcome.stats
    except ExtractionError as exc:
        _fail(document, run, exc.code, exc.params, telemetry)
    except Exception:
        # Sem valores no log: só o tipo e o traceback (que não carrega dado de exame).
        logger.exception("Falha inesperada na extração do laudo %s", document.pk)
        _fail(document, run, ErrorCode.EXTRACTION_FAILED, {}, telemetry)
    finally:
        # O PDF só existe durante a extração, com sucesso ou falha. Apagar vem primeiro:
        # não depende do banco.
        delete_upload(document.upload_path)
        document.upload_path = ""
        document.save(update_fields=["upload_path", "updated_at"])
        if run is not None:
            run.finished_at = timezone.now()
            run.save()
        _broadcast(user_id, document.pk, document.status)


def _fail(
    document: ExamDocument,
    run: ExtractionRun | None,
    code: ErrorCode,
    params: dict,
    telemetry: LLMTelemetry,
) -> None:
    document.status = DocumentStatus.FAILED
    document.error_code = str(code)
    document.error_params = params
    document.save(update_fields=["status", "error_code", "error_params", "updated_at"])
    if run is not None:
        run.status = RunStatus.FAILED
        run.error_code = str(code)
        run.error_params = params
        run.stats = telemetry.stats()


@shared_task
def sweep_orphans(max_age_hours: float | None = None) -> dict:
    """Limpa o que uma extração interrompida (worker morto, Redis fora) deixou para trás.

    1. laudos presos em EXTRACTING/UPLOADED há mais de `max_age_hours` viram FAILED
       (e perdem o PDF), para o usuário poder apagar/reenviar ou reprocessar;
    2. arquivos do diretório privado mais velhos que o limite são apagados;
    3. `upload_path` que aponta para arquivo inexistente é limpo.

    Devolve só contagens (nada de nomes de arquivo ou dados de exame).
    """
    hours = settings.EXAMS_ORPHAN_HOURS if max_age_hours is None else max_age_hours
    now = timezone.now()
    cutoff = now - timedelta(hours=hours)
    counts = {"documents_failed": 0, "files_deleted": 0, "paths_cleared": 0}

    stuck = ExamDocument.objects.filter(
        status__in=[DocumentStatus.EXTRACTING, DocumentStatus.UPLOADED], updated_at__lt=cutoff
    )
    for document in stuck:
        delete_upload(document.upload_path)
        document.status = DocumentStatus.FAILED
        document.error_code = str(ErrorCode.EXTRACTION_FAILED)
        document.error_params = {}
        document.upload_path = ""
        document.save(
            update_fields=["status", "error_code", "error_params", "upload_path", "updated_at"]
        )
        document.runs.filter(status__in=[RunStatus.QUEUED, RunStatus.RUNNING]).update(
            status=RunStatus.FAILED,
            error_code=str(ErrorCode.EXTRACTION_FAILED),
            finished_at=now,
        )
        counts["documents_failed"] += 1

    cutoff_ts = cutoff.timestamp()
    with os.scandir(upload_dir()) as entries:
        for entry in entries:
            try:
                if not entry.is_file(follow_symlinks=False):
                    continue
                if entry.stat(follow_symlinks=False).st_mtime >= cutoff_ts:
                    continue
                os.unlink(entry.path)
                counts["files_deleted"] += 1
            except OSError:
                logger.warning("Não foi possível apagar um arquivo temporário órfão")

    for document in ExamDocument.objects.exclude(upload_path="").iterator():
        if not Path(document.upload_path).exists():
            document.upload_path = ""
            document.save(update_fields=["upload_path", "updated_at"])
            counts["paths_cleared"] += 1

    return counts
