"""Checagens de configuração (`manage.py check`)."""

from django.conf import settings
from django.core.checks import Warning, register


@register()
def inline_mode_needs_no_redis(app_configs, **kwargs):
    """No modo inline (`CELERY_TASK_ALWAYS_EAGER`) não há Redis: cache e canais não podem usá-lo."""
    if not settings.CELERY_TASK_ALWAYS_EAGER:
        return []
    messages = []
    backend = settings.CACHES.get("default", {}).get("BACKEND", "")
    if backend.endswith("RedisCache"):
        messages.append(
            Warning(
                "CELERY_TASK_ALWAYS_EAGER está ligado, mas o cache ainda usa Redis.",
                hint="Sem Redis na nuvem, defina DJANGO_CACHE=db (e rode createcachetable).",
                id="exams.W001",
            )
        )
    layer = settings.CHANNEL_LAYERS.get("default", {}).get("BACKEND", "")
    if layer.endswith("RedisChannelLayer"):
        messages.append(
            Warning(
                "CELERY_TASK_ALWAYS_EAGER está ligado, mas a camada de canais usa Redis.",
                hint="Sem Redis, defina DJANGO_CHANNEL_LAYER=memory (ou none).",
                id="exams.W002",
            )
        )
    return messages
