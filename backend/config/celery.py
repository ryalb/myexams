import logging
import logging.config
import os

from celery import Celery
from celery.signals import setup_logging, worker_ready
from django.conf import settings

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

app = Celery("myexams")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()


@setup_logging.connect
def config_loggers(*args, **kwargs):
    logging.config.dictConfig(settings.LOGGING)


@worker_ready.connect
def sweep_orphans_on_start(*args, **kwargs):
    """Ao subir o worker, limpa PDFs e laudos que um worker anterior deixou presos."""
    from apps.exams.tasks import sweep_orphans

    logger = logging.getLogger(__name__)
    try:
        counts = sweep_orphans()
    except Exception:
        logger.exception("Falha na varredura de uploads órfãos")
    else:
        logger.info("Varredura de uploads órfãos: %s", counts)
